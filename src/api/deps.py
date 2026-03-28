"""FastAPI dependencies for authentication."""

from __future__ import annotations

from fastapi import Cookie, Request
from loguru import logger

from src.db.supabase import get_admin_client


async def get_session_user(
    request: Request,
    session_token: str | None = Cookie(None),
    refresh_token: str | None = Cookie(None),
) -> dict | None:
    """Return the current authenticated user, or None.

    Tries the access token first. If expired, attempts a refresh using the
    refresh token cookie and attaches new tokens to request.state so the
    route can set updated cookies on the response.
    """
    if not session_token:
        return None

    db = get_admin_client()
    request.state.new_tokens = None

    # Try current access token
    try:
        resp = db.auth.get_user(session_token)
        if resp.user:
            return {"id": resp.user.id, "email": resp.user.email}
    except Exception:
        pass

    # Access token invalid/expired — try refresh
    if refresh_token:
        try:
            session = db.auth.refresh_session(refresh_token)
            if session.user and session.session:
                request.state.new_tokens = (
                    session.session.access_token,
                    session.session.refresh_token,
                )
                return {"id": session.user.id, "email": session.user.email}
        except Exception as exc:
            logger.debug("Token refresh failed: {}", exc)

    return None
