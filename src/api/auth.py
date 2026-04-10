"""Authentication and account-connection routes."""

from __future__ import annotations

import secrets
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Cookie, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from loguru import logger
from spotipy.oauth2 import SpotifyOAuth

from src.api.approval_tokens import create_token, validate_and_consume
from src.api.deps import get_session_user
from src.collectors.spotify import SCOPES
from src.config import settings
from src.db.supabase import approve_by_email, get_admin_client, upsert_connected_account

router = APIRouter()
templates = Jinja2Templates(directory="src/web/templates")

_ACCESS_COOKIE_MAX_AGE = 60 * 60 * 24 * 7       # 7 days
_REFRESH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30     # 30 days
_COOKIE_OPTS = dict(
    httponly=True,
    samesite="lax",
    secure=(settings.app_environment == "production"),
    path="/",
)


# ─────────────────────────────────────────
# Pages
# ─────────────────────────────────────────


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user=Depends(get_session_user)) -> HTMLResponse:
    if user:
        return RedirectResponse("/")
    return templates.TemplateResponse(request, "login.html")


@router.get("/pending", response_class=HTMLResponse)
async def pending_page(request: Request, user=Depends(get_session_user)) -> HTMLResponse:
    if not user:
        return RedirectResponse("/login")
    return templates.TemplateResponse(request, "pending.html", {"email": user["email"]})


@router.get("/connect", response_class=HTMLResponse)
async def connect_page(request: Request, user=Depends(get_session_user)) -> HTMLResponse:
    if not user:
        return RedirectResponse("/login")
    # Check what's already connected
    db = get_admin_client()
    accounts = (
        db.table("connected_accounts")
        .select("platform,username,last_synced")
        .eq("user_id", user["id"])
        .execute()
    )
    connected = {row["platform"]: row for row in (accounts.data or [])}
    return templates.TemplateResponse(
        request, "connect.html", {"user": user, "connected": connected}
    )


# ─────────────────────────────────────────
# Signup / Login / Logout
# ─────────────────────────────────────────


async def _notify_signup_telegram(email: str) -> None:
    """Send a Telegram notification to Marvin about a new signup."""
    bot_token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id
    if not bot_token or not chat_id:
        logger.warning("Telegram notification skipped — bot token or chat_id not configured")
        return

    approval_token = create_token(email)
    approve_url = f"{settings.app_host}/admin/approve?email={quote(email)}&token={quote(approval_token)}"
    text = (
        f"\U0001f195 New Frequenz signup: {email}\n\n"
        f"Approve: {approve_url}"
    )
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": text})
            if resp.status_code != 200:
                logger.warning("Telegram notification failed: {}", resp.text)
    except Exception as exc:
        logger.warning("Telegram notification error: {}", exc)


@router.post("/auth/signup")
async def signup(
    email: str = Form(...),
    password: str = Form(...),
) -> Response:
    db = get_admin_client()
    try:
        db.auth.sign_up({"email": email, "password": password})
        # Profile auto-created by DB trigger with is_approved=false
        # Notify Marvin via Telegram (fire-and-forget)
        await _notify_signup_telegram(email)
        # Redirect to pending — they need to verify email first
        resp = RedirectResponse("/pending", status_code=303)
        return resp
    except Exception as exc:
        logger.warning("Signup failed for {}: {}", email, exc)
        resp = RedirectResponse("/login?error=signup_failed", status_code=303)
        return resp


@router.post("/auth/login")
async def login(
    email: str = Form(...),
    password: str = Form(...),
) -> Response:
    db = get_admin_client()
    try:
        result = db.auth.sign_in_with_password({"email": email, "password": password})
        session = result.session
        if not session:
            return RedirectResponse("/login?error=invalid_credentials", status_code=303)

        # Check approval status
        profile = db.table("profiles").select("is_approved").eq("id", result.user.id).single().execute()
        is_approved = profile.data and profile.data.get("is_approved", False)

        redirect_to = "/" if is_approved else "/pending"
        resp = RedirectResponse(redirect_to, status_code=303)
        resp.set_cookie(
            "session_token",
            session.access_token,
            max_age=_ACCESS_COOKIE_MAX_AGE,
            **_COOKIE_OPTS,
        )
        resp.set_cookie(
            "refresh_token",
            session.refresh_token,
            max_age=_REFRESH_COOKIE_MAX_AGE,
            **_COOKIE_OPTS,
        )
        return resp
    except Exception as exc:
        logger.warning("Login failed for {}: {}", email, exc)
        return RedirectResponse("/login?error=invalid_credentials", status_code=303)


@router.get("/auth/logout")
async def logout() -> Response:
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session_token")
    resp.delete_cookie("refresh_token")
    return resp


# ─────────────────────────────────────────
# Admin approval endpoint
# ─────────────────────────────────────────


@router.get("/admin/approve")
async def admin_approve_page(
    email: str = Query(...),
    token: str = Query(...),
    request: Request = None,
) -> HTMLResponse:
    """Show a confirmation page with a button that POSTs to actually approve.

    The URL contains an email in the query string — we emit noindex / no-store
    to minimise the chance of it being cached or indexed anywhere.
    """
    import html as _html
    safe_email = _html.escape(email)
    safe_token = _html.escape(token)
    body = f"""<!DOCTYPE html>
<html><head><title>Approve User</title>
<meta name=\"robots\" content=\"noindex,nofollow,noarchive\">
<style>body{{font-family:system-ui;max-width:420px;margin:60px auto;text-align:center}}
button{{background:#22c55e;color:#fff;border:none;padding:12px 32px;border-radius:8px;font-size:16px;cursor:pointer}}
button:hover{{background:#16a34a}}</style></head>
<body><h2>Approve user?</h2><p>{safe_email}</p>
<form method=\"POST\" action=\"/admin/approve\">
<input type=\"hidden\" name=\"email\" value=\"{safe_email}\">
<input type=\"hidden\" name=\"token\" value=\"{safe_token}\">
<button type=\"submit\">Approve</button>
</form></body></html>"""
    return HTMLResponse(
        content=body,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, private",
            "X-Robots-Tag": "noindex, nofollow, noarchive",
            "Referrer-Policy": "no-referrer",
        },
    )


@router.post("/admin/approve")
async def admin_approve(
    email: str = Form(...),
    token: str = Form(...),
) -> JSONResponse:
    """Approve a pending user. Validates a one-time token (not the master key)."""
    if not validate_and_consume(token, email):
        return JSONResponse({"error": "invalid or expired token"}, status_code=403)

    ok = approve_by_email(email)
    if ok:
        return JSONResponse({"status": "approved", "email": email})
    return JSONResponse({"error": "user not found", "email": email}, status_code=404)


# ─────────────────────────────────────────
# Spotify OAuth (per-user)
# ─────────────────────────────────────────


def _spotify_oauth() -> SpotifyOAuth:
    return SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.effective_spotify_redirect_uri,
        scope=SCOPES,
    )


@router.get("/auth/spotify")
async def spotify_connect(request: Request, user=Depends(get_session_user)) -> Response:
    """Start Spotify OAuth for the logged-in user."""
    if not user:
        return RedirectResponse("/login")

    state = secrets.token_urlsafe(16)
    # Store state in a short-lived cookie to verify on callback
    auth = _spotify_oauth()
    auth_url = auth.get_authorize_url(state=state)

    resp = RedirectResponse(auth_url)
    resp.set_cookie("oauth_state", state, httponly=True, max_age=300, samesite="lax")
    return resp


@router.get("/auth/spotify/callback")
async def spotify_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    oauth_state: str | None = Cookie(None),
    user=Depends(get_session_user),
) -> Response:
    """Handle Spotify OAuth callback and store tokens in Supabase."""
    if error:
        logger.warning("Spotify OAuth error: {}", error)
        return RedirectResponse("/connect?error=spotify_denied")

    if not user:
        return RedirectResponse("/login")

    # CSRF check
    if not state or state != oauth_state:
        logger.warning("Spotify OAuth state mismatch for user {}", user["id"])
        return RedirectResponse("/connect?error=state_mismatch")

    try:
        auth = _spotify_oauth()
        token_info = auth.get_access_token(code, as_dict=True)
    except Exception as exc:
        logger.error("Spotify token exchange failed: {}", exc)
        return RedirectResponse("/connect?error=token_exchange_failed")

    upsert_connected_account(
        user_id=user["id"],
        platform="spotify",
        access_token=token_info["access_token"],
        refresh_token=token_info.get("refresh_token"),
        username=None,  # will be fetched on first pipeline run
    )
    logger.info("Spotify connected for user {}", user["id"])

    resp = RedirectResponse("/connect?spotify=connected")
    resp.delete_cookie("oauth_state")
    return resp


# ─────────────────────────────────────────
# SoundCloud (username-based, no OAuth)
# ─────────────────────────────────────────


@router.post("/auth/soundcloud")
async def soundcloud_connect(
    username: str = Form(...),
    user=Depends(get_session_user),
) -> Response:
    """Store a SoundCloud username for the logged-in user."""
    if not user:
        return RedirectResponse("/login")

    upsert_connected_account(
        user_id=user["id"],
        platform="soundcloud",
        username=username.strip(),
    )
    logger.info("SoundCloud username '{}' saved for user {}", username, user["id"])
    return RedirectResponse("/connect?soundcloud=connected", status_code=303)
