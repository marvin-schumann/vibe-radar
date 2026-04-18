"""FastAPI web application for Frequenz."""

from __future__ import annotations

import asyncio
import json
import math
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from spotipy.cache_handler import CacheHandler

from src.api.auth import router as auth_router
from src.api.deps import get_session_user
from src.api.scan import router as scan_router
from src.config import settings
from src.integrations import brevo as brevo_integration
from src.db.supabase import (
    delete_user_account,
    export_user_data,
    get_admin_client,
    is_approved,
    is_pro,
    set_first_match_at,
    submit_nps,
    upsert_connected_account,
)
from src.models import Match, MatchType, TasteProfile


class _SupabaseCacheHandler(CacheHandler):
    """Spotipy CacheHandler that persists refreshed tokens back to Supabase."""

    def __init__(self, user_id: str) -> None:
        self._user_id = user_id
        self._token_info: dict | None = None

    def get_cached_token(self) -> dict | None:
        return self._token_info

    def save_token_to_cache(self, token_info: dict) -> None:
        self._token_info = token_info
        try:
            upsert_connected_account(
                user_id=self._user_id,
                platform="spotify",
                access_token=token_info["access_token"],
                refresh_token=token_info.get("refresh_token"),
            )
            logger.info("Spotify token refreshed and saved for user {}", self._user_id)
        except Exception as exc:
            logger.warning("Failed to persist refreshed Spotify token: {}", exc)

DATA_DIR = Path(__file__).parent.parent.parent / "data"


# ---------------------------------------------------------------------------
# App lifespan (scheduler start/stop)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    from src.api.scheduler import start_scheduler, stop_scheduler

    start_scheduler()
    yield
    stop_scheduler()


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Frequenz", version="1.0.0", lifespan=lifespan)

# ─────────────────────────────────────────
# Security middleware (DSGVO / Art. 32 – Security of processing)
# ─────────────────────────────────────────

# CORS: the landing page (frequenz.live on Vercel) makes cross-origin fetch
# calls to the API (app.frequenz.live on Hetzner) for /api/scan and /api/waitlist.
_cors_origins = (
    [
        "https://frequenz.live",
        "https://www.frequenz.live",
    ]
    if settings.app_environment == "production"
    else ["*"]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
    max_age=600,
)

# TrustedHost: protect against Host header attacks. In production this is
# restricted to the deployed hostname(s); in non-production any host is
# accepted for local development convenience.
_allowed_hosts = (
    ["app.frequenz.live", "frequenz.live", "www.frequenz.live", "localhost"]
    if settings.app_environment == "production"
    else ["*"]
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

app.include_router(auth_router)
app.include_router(scan_router)

app.mount(
    "/static",
    StaticFiles(directory="src/web/static"),
    name="static",
)

templates = Jinja2Templates(directory="src/web/templates")


# ─────────────────────────────────────────
# Security headers middleware (DSGVO Art. 32)
# ─────────────────────────────────────────

# Session / auth cookie lifetime (7 days access, 30 days refresh).
_ACCESS_COOKIE_MAX_AGE = 60 * 60 * 24 * 7
_REFRESH_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    # All scripts, fonts, styles and images are served from the same origin
    # (self-hosted Chart.js, self-hosted fonts). No third-party CDNs are
    # allowed. `connect-src` includes Supabase endpoints needed for auth.
    supabase_origin = ""
    if settings.supabase_url:
        try:
            from urllib.parse import urlparse
            p = urlparse(settings.supabase_url)
            supabase_origin = f"{p.scheme}://{p.netloc}"
        except Exception:
            supabase_origin = ""
    csp = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "object-src 'none'; "
        "script-src 'self' 'unsafe-inline'; "
        f"connect-src 'self' {supabase_origin}".strip() + "; "
        "font-src 'self'; "
        "img-src 'self' data: https:; "
        "style-src 'self' 'unsafe-inline'; "
        "media-src 'self'"
    )
    response.headers.setdefault("Content-Security-Policy", csp)
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    )
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    if settings.app_environment == "production":
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=63072000; includeSubDomains; preload",
        )
    return response


# ─────────────────────────────────────────
# Auth middleware: persist refreshed tokens
# ─────────────────────────────────────────


@app.middleware("http")
async def persist_refreshed_tokens(request: Request, call_next):
    request.state.new_tokens = None
    response = await call_next(request)
    if getattr(request.state, "new_tokens", None):
        access, refresh = request.state.new_tokens
        opts = dict(
            httponly=True,
            samesite="lax",
            secure=(settings.app_environment == "production"),
            path="/",
        )
        response.set_cookie(
            "session_token", access, max_age=_ACCESS_COOKIE_MAX_AGE, **opts
        )
        response.set_cookie(
            "refresh_token", refresh, max_age=_REFRESH_COOKIE_MAX_AGE, **opts
        )
    return response

# ---------------------------------------------------------------------------
# In-memory cache (per-user for authenticated runs, shared for snapshots)
# ---------------------------------------------------------------------------

_cache: dict[str, Any] = {
    "taste_profile": None,
    "matches": [],
    "events": [],
    "last_refresh": None,
    "refreshing": False,
}

# Per-user cache: user_id → same shape as _cache
_user_caches: dict[str, dict[str, Any]] = {}


def _user_cache(user_id: str) -> dict[str, Any]:
    if user_id not in _user_caches:
        _user_caches[user_id] = {
            "taste_profile": None,
            "matches": [],
            "last_refresh": None,
            "refreshing": False,
            "pipeline_status": None,  # {"step": str, "detail": str, "progress": int}
        }
    return _user_caches[user_id]


def _set_status(cache: dict, step: str, detail: str, progress: int) -> None:
    cache["pipeline_status"] = {"step": step, "detail": detail, "progress": progress}


def _load_snapshot_data() -> bool:
    """Load pre-collected data from snapshot files if available."""
    spotify_path = DATA_DIR / "spotify_snapshot.json"
    soundcloud_path = DATA_DIR / "soundcloud_snapshot.json"
    events_path = DATA_DIR / "madrid_events.json"

    if not events_path.exists():
        return False

    try:
        spotify_data = {}
        if spotify_path.exists():
            with open(spotify_path) as f:
                spotify_data = json.load(f)

        soundcloud_data = {}
        if soundcloud_path.exists():
            with open(soundcloud_path) as f:
                soundcloud_data = json.load(f)

        with open(events_path) as f:
            events_data = json.load(f)

        _cache["spotify_snapshot"] = spotify_data
        _cache["soundcloud_snapshot"] = soundcloud_data
        _cache["events_snapshot"] = events_data
        _cache["last_refresh"] = events_data.get("collected_at", datetime.now(tz=timezone.utc).isoformat())

        sp_count = len(spotify_data.get("artists", {}))
        sc_count = len(soundcloud_data.get("artists", {}))
        ev_count = len(events_data.get("events", []))
        logger.info("Loaded snapshot: {} Spotify + {} SoundCloud artists, {} events", sp_count, sc_count, ev_count)
        return True
    except Exception as exc:
        logger.warning("Failed to load snapshot data: {}", exc)
        return False


# Try loading snapshot on startup
_load_snapshot_data()


# ---------------------------------------------------------------------------
# Pipeline logic
# ---------------------------------------------------------------------------


async def _run_pipeline(user_id: str | None = None) -> None:
    """Run the full data collection and matching pipeline.

    If user_id is provided, uses that user's connected accounts from Supabase
    and stores results in their per-user cache. Falls back to the shared
    snapshot-based cache for anonymous / single-user operation.
    """
    from src.collectors.events.bandsintown import BandsintownCollector
    from src.collectors.events.resident_advisor import ResidentAdvisorCollector
    from src.collectors.events.songkick import SongkickCollector
    from src.collectors.soundcloud import SoundCloudCollector
    from src.collectors.spotify import SpotifyCollector
    from src.matching.exact import ExactMatcher
    from src.matching.vibe import VibeMatcher, build_taste_profile
    from src.matching.dj_event import match_events_via_dj_profiles
    from src.matching.dj_twin import get_user_genre_distribution

    cache = _user_cache(user_id) if user_id else _cache

    if cache.get("refreshing"):
        logger.info("Pipeline already running for user {}", user_id)
        return

    cache["refreshing"] = True
    cache["pipeline_status"] = None
    logger.info("Starting Frequenz pipeline (user={})", user_id or "anon")

    # -- 1. Collect user artists from music sources --
    all_artists = []

    if user_id:
        # --- Authenticated: fetch tokens from Supabase ---
        db = get_admin_client()
        accounts = (
            db.table("connected_accounts")
            .select("platform,access_token,refresh_token,username")
            .eq("user_id", user_id)
            .execute()
        )
        acct_map = {row["platform"]: row for row in (accounts.data or [])}

        # Spotify
        spotify_acct = acct_map.get("spotify")
        if spotify_acct and spotify_acct.get("access_token"):
            _set_status(cache, "Connecting to Spotify", "Authenticating...", 5)
            try:
                cache_handler = _SupabaseCacheHandler(user_id)
                spotify = SpotifyCollector.from_tokens(
                    access_token=spotify_acct["access_token"],
                    refresh_token=spotify_acct.get("refresh_token"),
                    cache_handler=cache_handler,
                )
                _set_status(cache, "Fetching Spotify artists", "Loading your top artists...", 15)
                spotify_artists = await spotify.collect_artists()
                all_artists.extend(spotify_artists)
                _set_status(cache, "Spotify done", f"{len(spotify_artists):,} artists loaded", 45)
                logger.info("Spotify: {} artists for user {}", len(spotify_artists), user_id)
            except Exception as exc:
                logger.warning("Spotify collection failed for user {}: {}", user_id, exc)

        # SoundCloud
        sc_acct = acct_map.get("soundcloud")
        sc_username = sc_acct.get("username") if sc_acct else None
        if sc_username:
            _set_status(cache, "Fetching SoundCloud artists", f"Scanning @{sc_username}...", 50)
            try:
                soundcloud = SoundCloudCollector(username=sc_username)
                sc_artists = await soundcloud.collect_artists()
                all_artists.extend(sc_artists)
                cache["sc_track_counts"] = soundcloud.track_counts
                cache["sc_liked_events"] = soundcloud.liked_events
                _set_status(cache, "SoundCloud done", f"{len(sc_artists):,} artists loaded", 60)
                logger.info("SoundCloud: {} artists for user {}", len(sc_artists), user_id)
            except Exception as exc:
                logger.warning("SoundCloud collection failed for user {}: {}", user_id, exc)
    else:
        # --- Anonymous / single-user: use local cache / env vars ---
        _set_status(cache, "Connecting to Spotify", "Authenticating...", 5)
        try:
            spotify = SpotifyCollector()
            spotify_artists = await spotify.collect_artists()
            all_artists.extend(spotify_artists)
            _set_status(cache, "Spotify done", f"{len(spotify_artists):,} artists loaded", 45)
            logger.info("Spotify: {} artists collected", len(spotify_artists))
        except Exception as exc:
            logger.warning("Spotify collection failed: {}", exc)

        try:
            if settings.soundcloud_username:
                _set_status(cache, "Fetching SoundCloud artists", "Scanning SoundCloud...", 50)
                soundcloud = SoundCloudCollector()
                sc_artists = await soundcloud.collect_artists()
                all_artists.extend(sc_artists)
                cache["sc_track_counts"] = soundcloud.track_counts
                cache["sc_liked_events"] = soundcloud.liked_events
                _set_status(cache, "SoundCloud done", f"{len(sc_artists):,} artists loaded", 60)
                logger.info("SoundCloud: {} artists collected", len(sc_artists))
        except Exception as exc:
            logger.warning("SoundCloud collection failed: {}", exc)

    if not all_artists:
        logger.warning("No artists collected from any source")
        cache["taste_profile"] = TasteProfile()
        cache["matches"] = []
        cache["last_refresh"] = datetime.now(tz=timezone.utc).isoformat()
        cache["refreshing"] = False
        cache["pipeline_status"] = None
        return

    # -- 2. Build taste profile --
    _set_status(cache, "Building taste profile", f"Analysing {len(all_artists):,} artists...", 65)
    taste_profile = build_taste_profile(all_artists)
    cache["taste_profile"] = taste_profile
    cache["artist_names"] = sorted(set(a.name for a in all_artists), key=str.lower)
    # Full artist objects for the Artists tab (deduplicated by normalized name)
    seen_artists: dict[str, dict[str, Any]] = {}
    for a in all_artists:
        key = a.normalized_name
        if key not in seen_artists:
            seen_artists[key] = {
                "name": a.name,
                "source": a.source.value,
                "image_url": a.image_url,
                "genres": a.genres,
                "popularity": a.popularity,
                "play_count": a.play_count,
            }
    cache["artist_objects"] = sorted(seen_artists.values(), key=lambda x: x["name"].lower())

    # -- 3. Collect events from all sources --
    _set_status(cache, "Scanning events in Madrid", "Checking Resident Advisor, Songkick...", 70)
    days = settings.days_ahead
    all_events = []

    # Collect from all event sources concurrently
    ra_collector = ResidentAdvisorCollector()
    bit_collector = BandsintownCollector()
    sk_collector = SongkickCollector()

    artist_names = [a.name for a in all_artists]

    results = await asyncio.gather(
        ra_collector.collect_events(days_ahead=days),
        bit_collector.collect_events(artist_names=artist_names, days_ahead=days),
        sk_collector.collect_events(days_ahead=days),
        return_exceptions=True,
    )

    source_names = ["Resident Advisor", "Bandsintown", "Songkick"]
    for source_name, result in zip(source_names, results):
        if isinstance(result, Exception):
            logger.warning("{} collection failed: {}", source_name, result)
        else:
            all_events.extend(result)
            logger.info("{}: {} events collected", source_name, len(result))

    logger.info("Total events collected: {}", len(all_events))
    _set_status(cache, "Finding your matches", f"{len(all_events)} events — matching against your artists...", 85)

    # -- 4. Run exact matching --
    exact_matcher = ExactMatcher()
    exact_matches = exact_matcher.match(all_artists, all_events)
    logger.info("Exact matches found: {}", len(exact_matches))

    # -- 5. Run vibe matching (excluding already-matched events) --
    matched_event_urls = {
        m.event.url for m in exact_matches if m.event.url
    }

    vibe_matcher = VibeMatcher()
    vibe_matches = vibe_matcher.match(
        all_artists,
        all_events,
        taste_profile=taste_profile,
        exclude_event_ids=matched_event_urls,
    )
    logger.info("Vibe matches found: {}", len(vibe_matches))

    # -- 6. Run DJ-profile-based event matching --
    matched_so_far = {
        m.event.url for m in exact_matches + vibe_matches if m.event.url
    }
    user_genres = get_user_genre_distribution(
        artists=cache.get("artist_objects"),
        taste_profile=taste_profile,
    )
    dj_event_matches = match_events_via_dj_profiles(
        user_genres,
        all_events,
        exclude_event_urls=matched_so_far,
    )
    logger.info("DJ-profile event matches found: {}", len(dj_event_matches))

    # -- 7. Combine and cache --
    all_matches = exact_matches + vibe_matches + dj_event_matches
    all_matches.sort(key=lambda m: m.sort_key)

    cache["matches"] = all_matches
    cache["last_refresh"] = datetime.now(tz=timezone.utc).isoformat()
    cache["refreshing"] = False
    cache["pipeline_status"] = None

    # Record first match timestamp for NPS prompt (no-op if already set)
    if user_id and all_matches:
        try:
            set_first_match_at(user_id)
        except Exception as exc:
            logger.warning("Could not set first_match_at for user {}: {}", user_id, exc)

    logger.info(
        "Pipeline complete: {} total matches ({} exact, {} vibe, {} dj-profile)",
        len(all_matches),
        len(exact_matches),
        len(vibe_matches),
        len(dj_event_matches),
    )


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _serialize_taste_profile(profile: TasteProfile | None) -> dict[str, Any]:
    """Convert a TasteProfile into a JSON-friendly dict."""
    if profile is None:
        return {
            "top_genres": [],
            "avg_features": None,
            "total_artists": 0,
            "sources": {},
        }

    features_dict = None
    if profile.avg_features is not None:
        features_dict = {
            "danceability": round(profile.avg_features.danceability, 3),
            "energy": round(profile.avg_features.energy, 3),
            "tempo": round(profile.avg_features.tempo, 1),
            "valence": round(profile.avg_features.valence, 3),
            "acousticness": round(profile.avg_features.acousticness, 3),
            "instrumentalness": round(profile.avg_features.instrumentalness, 3),
            "liveness": round(profile.avg_features.liveness, 3),
            "speechiness": round(profile.avg_features.speechiness, 3),
        }

    return {
        "top_genres": [
            {"genre": genre, "count": count}
            for genre, count in profile.top_genres[:20]
        ],
        "avg_features": features_dict,
        "features_estimated": profile.features_estimated,
        "total_artists": profile.total_artists,
        "sources": profile.sources,
    }


def _serialize_match(match: Match) -> dict[str, Any]:
    """Convert a Match into a JSON-friendly dict."""
    event = match.event
    venue_dict = None
    if event.venue is not None:
        venue_dict = {
            "name": event.venue.name,
            "city": event.venue.city,
            "address": event.venue.address,
        }

    return {
        "event": {
            "name": event.name,
            "date": event.date.isoformat(),
            "url": event.url,
            "image_url": event.image_url,
            "source": event.source.value,
            "artists": event.artists,
            "venue": venue_dict,
            "price": event.price,
            "description": event.description,
        },
        "matched_artist": {
            "name": match.matched_artist.name,
            "source": match.matched_artist.source.value,
            "image_url": match.matched_artist.image_url,
            "genres": match.matched_artist.genres[:5],
        },
        "event_artist_name": match.event_artist_name,
        "match_type": match.match_type.value,
        "confidence": match.confidence,
        "match_reason": match.match_reason,
    }


# ---------------------------------------------------------------------------
# Routes: Legal pages (DSGVO compliance)
# ---------------------------------------------------------------------------


@app.get("/impressum", response_class=HTMLResponse)
async def impressum(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "impressum.html")


@app.get("/datenschutz", response_class=HTMLResponse)
async def datenschutz(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "datenschutz.html")


# ---------------------------------------------------------------------------
# Routes: DSGVO data-subject endpoints (Art. 15 / 17 / 20)
# ---------------------------------------------------------------------------


# Very small in-process rate limiter for data-subject endpoints so they
# cannot be abused (e.g. an attacker hammering /api/me/export to enumerate
# accounts). Keyed per user-id.
_dsr_last_call: dict[str, float] = {}
_DSR_COOLDOWN_SECONDS = 60


def _rate_limit_dsr(user_id: str) -> bool:
    """Return True if a data-subject request is allowed, False if throttled."""
    import time
    now = time.monotonic()
    last = _dsr_last_call.get(user_id, 0.0)
    if now - last < _DSR_COOLDOWN_SECONDS:
        return False
    _dsr_last_call[user_id] = now
    return True


@app.get("/api/me/export")
async def export_my_data(user=Depends(get_session_user)) -> Response:
    """Art. 15 DSGVO (access) / Art. 20 DSGVO (portability).

    Returns a JSON file containing every personal data record associated
    with the authenticated user. OAuth tokens are redacted.
    """
    if not user:
        return JSONResponse({"error": "authentication required"}, status_code=401)
    if not _rate_limit_dsr(user["id"]):
        return JSONResponse(
            {"error": "rate limited — please wait before requesting another export"},
            status_code=429,
        )
    data = export_user_data(user["id"])
    logger.info("Data export requested by user {}", user["id"])
    return JSONResponse(
        content=data,
        headers={
            "Content-Disposition": 'attachment; filename="frequenz-data-export.json"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/me/delete")
async def delete_my_account(request: Request, user=Depends(get_session_user)) -> Response:
    """Art. 17 DSGVO — right to erasure.

    Permanently deletes the user and all personal data. Requires
    ``confirm=DELETE`` in the form body to avoid accidental calls.
    Session cookies are cleared on success.
    """
    if not user:
        return JSONResponse({"error": "authentication required"}, status_code=401)
    if not _rate_limit_dsr(user["id"]):
        return JSONResponse(
            {"error": "rate limited — please wait before retrying"},
            status_code=429,
        )
    form = await request.form()
    if form.get("confirm") != "DELETE":
        return JSONResponse(
            {"error": "missing confirmation — send form field confirm=DELETE"},
            status_code=400,
        )

    ok = delete_user_account(user["id"])
    if not ok:
        return JSONResponse({"error": "deletion failed"}, status_code=500)

    logger.info("Account deleted by user {}", user["id"])
    resp = JSONResponse({"status": "deleted"})
    resp.delete_cookie("session_token", path="/")
    resp.delete_cookie("refresh_token", path="/")
    return resp


# ---------------------------------------------------------------------------
# Waitlist (replaces Formspree)
# ---------------------------------------------------------------------------

# Per-IP rate limit for waitlist signups — prevents spam without breaking real users
_WAITLIST_COOLDOWN_SECONDS = 30
_waitlist_last_call: dict[str, float] = {}


def _rate_limit_waitlist(ip: str) -> bool:
    """Return True if the call is allowed, False if rate-limited."""
    import time

    now = time.time()
    last = _waitlist_last_call.get(ip, 0.0)
    if now - last < _WAITLIST_COOLDOWN_SECONDS:
        return False
    _waitlist_last_call[ip] = now
    return True


def _client_ip(request: Request) -> str:
    """Best-effort client IP for rate limiting only — never logged or stored."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.post("/api/waitlist")
async def join_waitlist(request: Request) -> Response:
    """Public waitlist signup endpoint — wraps Brevo contacts API.

    Replaces the Formspree form on the landing page. POST a JSON body with
    {"email": "...", "consent": true, "city": "Madrid"} (city optional).

    Legal basis: Art. 6 Abs. 1 lit. a DSGVO (consent).
    Processor: Brevo / Sendinblue SAS, Paris, France (EU).
    Retention: until launch announcement OR explicit withdrawal, max 24 months.
    """
    if not _rate_limit_waitlist(_client_ip(request)):
        return JSONResponse(
            {"error": "rate limited — please wait a moment before retrying"},
            status_code=429,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    email = (body.get("email") or "").strip().lower()
    consent = bool(body.get("consent"))
    city = (body.get("city") or "").strip() or None
    first_name = (body.get("first_name") or "").strip() or None

    # Minimal validation — defer email-format checking to Brevo
    if not email or "@" not in email or len(email) > 254:
        return JSONResponse({"error": "invalid email"}, status_code=400)
    if not consent:
        return JSONResponse(
            {
                "error": "consent required",
                "detail": "Art. 6 Abs. 1 lit. a DSGVO — explicit consent must be given",
            },
            status_code=400,
        )

    try:
        result = await brevo_integration.add_waitlist_contact(
            email,
            first_name=first_name,
            city=city,
            source="landing-page",
        )
    except brevo_integration.BrevoError as exc:
        logger.error("waitlist signup failed for {}: {}", email, exc)
        return JSONResponse(
            {"error": "waitlist signup failed — please try again later"},
            status_code=502,
        )

    logger.info("waitlist signup: {} (duplicate={})", email, result.get("duplicate", False))
    return JSONResponse({"status": "ok"})


# ---------------------------------------------------------------------------
# Routes: Pages
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, user=Depends(get_session_user)) -> HTMLResponse:
    """Render the main dashboard page (requires auth + approval)."""
    if not user:
        return RedirectResponse("/login")
    if not is_approved(user["id"]):
        return RedirectResponse("/pending")

    cache = _user_cache(user["id"])
    # Do NOT auto-run — user hits Refresh explicitly when they want fresh data.

    last_refresh = cache.get("last_refresh") or _cache.get("last_refresh")

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "city": settings.city,
            "last_refresh": last_refresh,
            "user": user,
            "is_pro": is_pro(user["id"]),
        },
    )


# ---------------------------------------------------------------------------
# Routes: API
# ---------------------------------------------------------------------------


@app.get("/api/taste")
async def get_taste_profile(user=Depends(get_session_user)) -> JSONResponse:
    """Return the user's taste profile as JSON."""
    # Try per-user live profile first, fall back to snapshot
    profile = _user_cache(user["id"]).get("taste_profile") if user else None
    if profile is None:
        profile = _cache.get("taste_profile")
    if profile is not None:
        last_refresh = (_user_cache(user["id"]).get("last_refresh") if user else None) or _cache.get("last_refresh")
        return JSONResponse(
            content={
                "taste_profile": _serialize_taste_profile(profile),
                "last_refresh": last_refresh,
            }
        )

    # Build from snapshot
    spotify_data = _cache.get("spotify_snapshot", {})
    soundcloud_data = _cache.get("soundcloud_snapshot", {})
    sp_artists = spotify_data.get("artists", {})
    sc_artists = soundcloud_data.get("artists", {})

    if not sp_artists and not sc_artists:
        return JSONResponse(content={"taste_profile": _serialize_taste_profile(None), "last_refresh": None})

    # Aggregate genres from Spotify (SoundCloud doesn't have genres)
    genre_count: dict[str, int] = {}
    for a in sp_artists.values():
        for g in a.get("genres", []):
            genre_count[g] = genre_count.get(g, 0) + 1
    top_genres = sorted(genre_count.items(), key=lambda x: -x[1])

    # Source breakdown
    sources = {}
    if sp_artists:
        sources["spotify"] = len(sp_artists)
    if sc_artists:
        sources["soundcloud"] = len(sc_artists)

    # Count unique combined
    all_names = set(n.lower().strip() for n in sp_artists) | set(n.lower().strip() for n in sc_artists)

    # Audio features (estimated from genre profile)
    avg_features = spotify_data.get("audio_features_estimated")

    return JSONResponse(
        content={
            "taste_profile": {
                "top_genres": [{"genre": g, "count": c} for g, c in top_genres[:20]],
                "avg_features": avg_features,
                "total_artists": len(all_names),
                "sources": sources,
            },
            "features_estimated": avg_features is not None,
            "last_refresh": _cache.get("last_refresh"),
        }
    )


@app.get("/api/debug-events")
async def debug_events(user=Depends(get_session_user)) -> JSONResponse:
    """Debug: show all events with their parsed artist lists."""
    cache = _user_cache(user["id"]) if user else _cache
    matches = cache.get("matches", [])
    # Get all events from the last pipeline run — stored on matches
    # Also show which user artists exist
    names = cache.get("artist_names", [])
    events_summary = []
    seen = set()
    for m in matches:
        key = m.event.url or m.event.name
        if key not in seen:
            seen.add(key)
            events_summary.append({
                "name": m.event.name,
                "artists_on_event": m.event.artists,
                "matched_artist": m.matched_artist.name,
            })
    return JSONResponse(content={
        "total_user_artists": len(names),
        "matched_events": events_summary,
        "sample_user_artists": names[:50],
    })


@app.get("/api/depth-score")
async def get_depth_score(user=Depends(get_session_user)) -> JSONResponse:
    """Compute the Underground Depth Score from artist popularity data.

    Primary: 100 - avg(Spotify popularity).
    Fallback: SoundCloud play_count percentile (lower plays = more underground).
    """
    cache = _user_cache(user["id"]) if user else _cache
    artists = cache.get("artist_objects") or []

    # Try Spotify popularity first
    pops = [a["popularity"] for a in artists if a.get("popularity") is not None]

    if pops:
        avg_pop = sum(pops) / len(pops)
        score = round(100 - avg_pop)
        source_label = "spotify"
        sample_size = len(pops)
        # Top 5 most underground by lowest popularity
        underground_artists = sorted(
            [a for a in artists if a.get("popularity") is not None],
            key=lambda a: a["popularity"],
        )[:5]
        deepest = [
            {"name": a["name"], "popularity": a["popularity"], "image_url": a.get("image_url")}
            for a in underground_artists
        ]
    else:
        # Fallback: SoundCloud — combine play_count scaling with genre underground weight
        artists_with_plays = [
            a for a in artists if a.get("play_count") is not None and a["play_count"] > 0
        ]
        if not artists_with_plays:
            return JSONResponse(content={"score": None, "sample_size": 0})

        # --- Genre-based underground weight ---
        # SC play counts don't map to mainstream/underground well.
        # A trance DJ with 1M plays is still underground compared to pop.
        _UNDERGROUND_GENRES = {
            "trance", "psytrance", "progressive trance", "uplifting trance", "goa trance",
            "techno", "minimal techno", "dub techno", "acid techno", "industrial techno",
            "industrial", "minimal", "ambient", "dark ambient",
            "drone", "noise", "experimental", "idm",
            "jungle", "breakcore", "gabber", "hardcore",
            "dub", "dubstep",
        }
        _NEUTRAL_GENRES = {
            "house", "deep house", "tech house", "progressive house", "afro house",
            "disco", "nu-disco", "electro", "electronica",
            "drum and bass", "dnb", "uk bass", "breakbeat", "garage",
        }
        # Everything else (pop, hip-hop, r&b, latin, reggaeton, etc.) = mainstream

        genre_counts = {"underground": 0, "neutral": 0, "mainstream": 0}
        for a in artists:
            for g in a.get("genres") or []:
                gl = g.lower().strip()
                if any(ug in gl for ug in _UNDERGROUND_GENRES):
                    genre_counts["underground"] += 1
                elif any(ng in gl for ng in _NEUTRAL_GENRES):
                    genre_counts["neutral"] += 1
                else:
                    genre_counts["mainstream"] += 1

        total_genre_tags = sum(genre_counts.values()) or 1
        underground_frac = genre_counts["underground"] / total_genre_tags
        mainstream_frac = genre_counts["mainstream"] / total_genre_tags
        # Genre weight: -20 (all mainstream) to +20 (all underground)
        genre_weight = round((underground_frac - mainstream_frac) * 20)

        # --- Play count component ---
        # Use median instead of mean to resist outlier skew
        play_counts = sorted(a["play_count"] for a in artists_with_plays)
        n = len(play_counts)
        median_play = play_counts[n // 2] if n % 2 else (play_counts[n // 2 - 1] + play_counts[n // 2]) / 2

        # Log-scale mapping: median plays → base score
        # <1K → 70, ~10K → 55, ~100K → 40, ~1M → 25, >10M → 10
        if median_play <= 0:
            play_score = 70
        else:
            log_play = math.log10(median_play)
            # Linear map: log10(1000)=3 → 70, log10(10_000_000)=7 → 10
            play_score = max(10, min(70, round(70 - (log_play - 3) * 15)))

        # Combine: play_count base + genre weight, clamped to 1-99
        score = max(1, min(99, play_score + genre_weight))

        source_label = "soundcloud"
        sample_size = n
        # Top 5 most underground by lowest play_count
        underground_artists = sorted(artists_with_plays, key=lambda a: a["play_count"])[:5]
        deepest = [
            {"name": a["name"], "play_count": a["play_count"], "image_url": a.get("image_url")}
            for a in underground_artists
        ]

    # Descriptive label
    if score >= 80:
        label = "Deep Underground"
        blurb = "Your taste lives in the shadows. Most people have never heard your artists."
    elif score >= 65:
        label = "Underground Explorer"
        blurb = "You dig deep. Your library is packed with artists the mainstream hasn't discovered."
    elif score >= 50:
        label = "Underground-Leaning"
        blurb = "You're ahead of the curve. Your taste is credible but not obscure."
    elif score >= 35:
        label = "Balanced"
        blurb = "You mix underground with familiar. Good balance."
    elif score >= 20:
        label = "Mainstream-Leaning"
        blurb = "You know the hits but keep an eye on what's next."
    else:
        label = "Mainstream"
        blurb = "Your taste is right in the cultural center."

    return JSONResponse(content={
        "score": score,
        "label": label,
        "blurb": blurb,
        "sample_size": sample_size,
        "source": source_label,
        "deepest_artists": deepest,
    })


@app.get("/api/taste-dna")
async def get_taste_dna(user=Depends(get_session_user)) -> JSONResponse:
    """Compute Taste DNA features from artist genre data."""
    from src.analytics.taste_dna import compute_taste_dna

    cache = _user_cache(user["id"]) if user else _cache
    artists = cache.get("artist_objects") or []
    if not artists:
        return JSONResponse(content={"error": "No artist data", "scene_city": None, "taste_tribe": None, "cross_genre_bridges": None, "dancefloor_ratio": None})

    result = compute_taste_dna(artists)
    return JSONResponse(content=result)


@app.get("/api/dj-twin")
async def get_dj_twin(user=Depends(get_session_user)) -> JSONResponse:
    """Return top 5 DJ twin matches based on genre similarity."""
    from src.matching.dj_twin import compute_dj_similarity, get_user_genre_distribution, load_dj_vectors

    cache = _user_cache(user["id"]) if user else _cache
    artists = cache.get("artist_objects") or []
    taste_profile = cache.get("taste_profile")

    # Build user's genre distribution
    user_genres = get_user_genre_distribution(artists=artists, taste_profile=taste_profile)
    if not user_genres:
        return JSONResponse(
            content={"error": "No genre data — run a refresh first", "matches": []},
            status_code=400,
        )

    # Load cached DJ vectors (read once per request — fast, ~100KB JSON)
    dj_vectors = load_dj_vectors()
    if not dj_vectors:
        return JSONResponse(
            content={"error": "DJ taste vectors not yet generated", "matches": []},
            status_code=503,
        )

    matches = compute_dj_similarity(user_genres, dj_vectors, top_n=5)
    twin = matches[0] if matches else None

    return JSONResponse(content={
        "twin": twin,
        "matches": matches,
        "total_djs_compared": len(dj_vectors),
        "headline": f"Your taste is {twin['similarity_pct']}% similar to {twin['name']}" if twin else None,
    })


@app.get("/api/artists")
async def get_artists(user=Depends(get_session_user)) -> JSONResponse:
    """Return the collected artist list with full metadata from the last pipeline run."""
    cache = _user_cache(user["id"]) if user else _cache
    artists = cache.get("artist_objects")
    if artists is not None:
        return JSONResponse(content={"artists": artists, "total": len(artists)})
    # Backwards compat: fall back to name-only list from older pipeline runs
    names = cache.get("artist_names")
    if names is None:
        return JSONResponse(content={"artists": [], "total": 0, "message": "No data yet — hit refresh first"})
    return JSONResponse(content={
        "artists": [{"name": n, "source": None, "image_url": None, "genres": [], "popularity": None} for n in names],
        "total": len(names),
    })


@app.get("/api/events")
async def get_events(
    user=Depends(get_session_user),
    match_type: str | None = Query(
        default="all",
        description="Filter by match type: 'exact', 'vibe', or 'all'",
    ),
    days_ahead: int = Query(
        default=None,
        description="Only show events within this many days",
    ),
) -> JSONResponse:
    """Return upcoming matched events as JSON."""
    # Try per-user live matches first, fall back to shared cache
    live_matches: list[Match] = []
    if user:
        live_matches = _user_cache(user["id"]).get("matches", [])
    if not live_matches:
        live_matches = _cache.get("matches", [])
    if live_matches:
        filtered = live_matches
        if match_type and match_type != "all":
            try:
                mt = MatchType(match_type)
                filtered = [m for m in filtered if m.match_type == mt]
            except ValueError:
                pass
        last_refresh = (_user_cache(user["id"]).get("last_refresh") if user else None) or _cache.get("last_refresh")
        return JSONResponse(
            content={
                "matches": [_serialize_match(m) for m in filtered],
                "total": len(filtered),
                "match_type": match_type or "all",
                "last_refresh": last_refresh,
            }
        )

    # Fall back to snapshot data
    events_data = _cache.get("events_snapshot", {})
    snapshot_matches = events_data.get("matches", [])
    all_events = events_data.get("events", [])

    results = []
    # Serve exact matches from snapshot
    for m in snapshot_matches:
        artist_source = m.get("source", "spotify")
        if "soundcloud" in artist_source:
            artist_source_tag = "soundcloud"
        else:
            artist_source_tag = "spotify"
        entry = {
            "event": {
                "name": m.get("event", ""),
                "date": m.get("date", ""),
                "url": m.get("url", ""),
                "image_url": None,
                "source": m.get("event_source", m.get("source", "")).lower().replace(" ", "_"),
                "artists": [m.get("event_artist", "")],
                "venue": {"name": m.get("venue", ""), "city": "Madrid", "address": None},
                "price": None,
                "description": None,
            },
            "matched_artist": {
                "name": m.get("your_artist", ""),
                "source": artist_source_tag,
                "image_url": None,
                "genres": [],
            },
            "event_artist_name": m.get("event_artist", ""),
            "match_type": "exact" if m.get("score", 0) >= 95 else "vibe",
            "confidence": m.get("score", 0) / 100.0,
            "match_reason": f"{'Exact' if m.get('score', 0) >= 95 else 'Close'} match ({m.get('score', 0)}%): {m.get('your_artist', '')} in your library",
        }
        if match_type == "all" or match_type is None or entry["match_type"] == match_type:
            results.append(entry)

    # Also include vibe-matching events (keyword matches)
    import re
    vibe_keywords = {"techno", "hypertechno", "hard techno", "trance", "drum and bass",
                     "house", "edm", "minimal", "hardstyle", "frenchcore", "hardcore",
                     "tekno", "acid", "psytrance", "melodic", "gabber", "rave",
                     "electronic", "bass", "dnb", "hard house"}
    matched_event_names = {m.get("event", "") for m in snapshot_matches}

    for ev in all_events:
        if ev["name"] in matched_event_names:
            continue
        name_lower = ev["name"].lower()
        artists_lower = " ".join(a.lower() for a in ev.get("artists", []))
        combined = f"{name_lower} {artists_lower}"
        matched_kw = [k for k in vibe_keywords if k in combined]
        if matched_kw and (match_type in ("all", None, "vibe")):
            results.append({
                "event": {
                    "name": ev["name"],
                    "date": ev.get("date", "")[:10] if ev.get("date") else "",
                    "url": ev.get("url", ""),
                    "image_url": None,
                    "source": ev.get("source", "").lower().replace(" ", "_"),
                    "artists": ev.get("artists", []),
                    "venue": {"name": ev.get("venue", ""), "city": "Madrid", "address": None},
                    "price": None,
                    "description": None,
                },
                "matched_artist": {
                    "name": ", ".join(matched_kw),
                    "source": "genre_match",
                    "image_url": None,
                    "genres": matched_kw,
                },
                "event_artist_name": ", ".join(ev.get("artists", [])[:3]),
                "match_type": "vibe",
                "confidence": min(len(matched_kw) * 0.3, 1.0),
                "match_reason": f"Genre match: {', '.join(matched_kw)}",
            })

    return JSONResponse(
        content={
            "matches": results,
            "total": len(results),
            "match_type": match_type or "all",
            "last_refresh": _cache.get("last_refresh"),
        }
    )


@app.get("/api/pipeline-status")
async def pipeline_status(user=Depends(get_session_user)) -> JSONResponse:
    """Return current pipeline status for the loading overlay."""
    if not user:
        return JSONResponse({"running": False, "status": None})
    cache = _user_cache(user["id"])
    return JSONResponse({
        "running": bool(cache.get("refreshing")),
        "status": cache.get("pipeline_status"),
        "last_refresh": cache.get("last_refresh"),
    })


@app.post("/api/nps")
async def submit_nps_response(request: Request, user=Depends(get_session_user)) -> JSONResponse:
    """Store user's NPS response (PMF survey)."""
    if not user:
        return JSONResponse({"status": "error"}, status_code=401)
    body = await request.json()
    score = body.get("score", "")
    if score not in ("very_disappointed", "somewhat_disappointed", "not_disappointed"):
        return JSONResponse({"status": "error", "message": "Invalid score"}, status_code=400)
    try:
        submit_nps(user["id"], score)
    except Exception as exc:
        logger.error("Failed to save NPS for user {}: {}", user["id"], exc)
        return JSONResponse({"status": "error"}, status_code=500)
    return JSONResponse({"status": "ok"})


@app.get("/api/nps-status")
async def nps_status(user=Depends(get_session_user)) -> JSONResponse:
    """Return whether the NPS modal should be shown."""
    if not user:
        return JSONResponse({"show": False})
    from src.db.supabase import get_profile
    from datetime import timezone as tz
    profile = get_profile(user["id"])
    if not profile or profile.get("nps_submitted"):
        return JSONResponse({"show": False})
    first_match_at = profile.get("first_match_at")
    if not first_match_at:
        return JSONResponse({"show": False})
    from datetime import datetime
    matched = datetime.fromisoformat(first_match_at.replace("Z", "+00:00"))
    hours_since = (datetime.now(tz.utc) - matched).total_seconds() / 3600
    return JSONResponse({"show": hours_since >= 24})


@app.get("/api/refresh")
async def refresh_data(user=Depends(get_session_user)) -> JSONResponse:
    """Trigger a fresh data collection + matching run for the current user."""
    from src.api.scheduler import _scrape_lock

    if not user:
        return JSONResponse({"status": "error", "message": "Not authenticated"}, status_code=401)

    cache = _user_cache(user["id"])
    if cache.get("refreshing"):
        return JSONResponse(
            content={"status": "already_running", "message": "A refresh is already in progress."},
            status_code=409,
        )

    if _scrape_lock.locked():
        return JSONResponse(
            content={"status": "already_running", "message": "Background event scrape in progress — try again shortly."},
            status_code=409,
        )

    try:
        await _run_pipeline(user_id=user["id"])
        return JSONResponse(
            content={
                "status": "ok",
                "message": "Pipeline refresh completed.",
                "last_refresh": cache.get("last_refresh"),
            }
        )
    except Exception as exc:
        cache["refreshing"] = False
        logger.error("Pipeline refresh failed for user {}: {}", user["id"], exc)
        return JSONResponse(
            content={"status": "error", "message": f"Pipeline refresh failed: {exc}"},
            status_code=500,
        )


@app.get("/api/scheduler/status")
async def scheduler_status(user=Depends(get_session_user)) -> JSONResponse:
    """Return the background event scraper status. Requires authentication."""
    if not user:
        return JSONResponse(content={"error": "unauthorized"}, status_code=401)

    from src.api.scheduler import get_scheduler_status

    return JSONResponse(content=get_scheduler_status())


# ---------------------------------------------------------------------------
# Routes: Cron — Monday Drop (retention ritual)
# ---------------------------------------------------------------------------


@app.post("/api/cron/monday-drop")
async def cron_monday_drop(
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> JSONResponse:
    """Send the weekly Monday Drop to every eligible user.

    Protected by a shared secret passed in the ``X-Cron-Secret`` header —
    the same value stored in ``settings.admin_secret_key``. Intended to
    be hit by a GitHub Actions cron or Coolify scheduled task at Monday
    08:00 local time.
    """
    if not settings.admin_secret_key:
        logger.error("monday-drop cron blocked: admin_secret_key not configured")
        raise HTTPException(status_code=503, detail="cron not configured")
    if not x_cron_secret or x_cron_secret != settings.admin_secret_key:
        logger.warning("monday-drop cron: unauthorized request")
        raise HTTPException(status_code=401, detail="unauthorized")

    from src.api.monday_drop import send_monday_drop_to_all_users

    logger.info("monday-drop cron: starting run")
    started = datetime.now(tz=timezone.utc)
    try:
        result = await send_monday_drop_to_all_users()
    except Exception as exc:
        logger.exception("monday-drop cron: batch failed: {}", exc)
        raise HTTPException(status_code=500, detail=f"batch failed: {exc}") from exc

    duration_ms = int((datetime.now(tz=timezone.utc) - started).total_seconds() * 1000)
    logger.info(
        "monday-drop cron: done sent={} failed={} duration_ms={}",
        result.get("sent"),
        result.get("failed"),
        duration_ms,
    )
    return JSONResponse(
        content={
            "sent": result.get("sent", 0),
            "failed": result.get("failed", 0),
            "skipped": result.get("skipped", 0),
            "duration_ms": duration_ms,
            "started_at": started.isoformat(),
        }
    )


# ---------------------------------------------------------------------------
# Routes: Shareable Cards
# ---------------------------------------------------------------------------


@app.get("/api/cards/all")
async def get_all_cards(user=Depends(get_session_user)) -> JSONResponse:
    """Return all 5 cards as base64-encoded PNGs in JSON."""
    import base64

    from src.analytics.taste_dna import compute_taste_dna
    from src.cards.renderer import render_all_cards

    cache = _user_cache(user["id"]) if user else _cache
    artists = cache.get("artist_objects") or []
    if not artists:
        return JSONResponse({"error": "No artist data — run a refresh first"}, status_code=400)

    dna = compute_taste_dna(artists)

    profile = cache.get("taste_profile")
    if profile:
        dna["top_genres"] = [
            {"genre": g, "percentage": round(c / max(profile.total_artists, 1) * 100)}
            for g, c in profile.top_genres[:5]
        ]
        dna["total_artists"] = profile.total_artists
    else:
        dna.setdefault("total_artists", len(artists))
        dna.setdefault("top_genres", [])

    cards = render_all_cards(dna)
    return JSONResponse({
        name: base64.b64encode(png).decode() for name, png in cards.items()
    })


@app.get("/api/cards/{card_name}.png")
async def get_card_png(card_name: str, user=Depends(get_session_user)) -> Response:
    """Return a single shareable card as image/png."""
    from src.analytics.taste_dna import compute_taste_dna
    from src.cards.renderer import CARD_REGISTRY, render_card

    if card_name not in CARD_REGISTRY:
        return JSONResponse({"error": f"Unknown card: {card_name}"}, status_code=404)

    cache = _user_cache(user["id"]) if user else _cache
    artists = cache.get("artist_objects") or []
    if not artists:
        return JSONResponse({"error": "No artist data — run a refresh first"}, status_code=400)

    dna = compute_taste_dna(artists)

    # Merge top_genres + total_artists from taste profile
    profile = cache.get("taste_profile")
    if profile:
        dna["top_genres"] = [
            {"genre": g, "percentage": round(c / max(profile.total_artists, 1) * 100)}
            for g, c in profile.top_genres[:5]
        ]
        dna["total_artists"] = profile.total_artists
    else:
        dna.setdefault("total_artists", len(artists))
        dna.setdefault("top_genres", [])

    png_bytes = render_card(card_name, dna)
    return Response(content=png_bytes, media_type="image/png")


# ---------------------------------------------------------------------------
# Routes: Public Reveal (no auth — accessible via scan task_id)
# ---------------------------------------------------------------------------


@app.get("/reveal/{task_id}", response_class=HTMLResponse)
async def reveal_page(request: Request, task_id: str) -> HTMLResponse:
    """Public reveal page — shows the full analysis for a completed scan.

    No auth required. Uses the scan task's cached data to render the same
    analysis template the authenticated dashboard uses.
    """
    from src.api.scan import _TASKS

    task = _TASKS.get(task_id)
    if not task or task.status != "done" or not task.result:
        return HTMLResponse(
            "<h1>Scan not found</h1><p>This scan may have expired or is still processing. "
            "<a href='/'>Try again</a></p>",
            status_code=404,
        )

    return templates.TemplateResponse(
        request,
        "index.html",
        {"user": None, "scan_task_id": task_id},
    )


@app.get("/api/analysis/scan/{task_id}")
async def get_scan_analysis(task_id: str) -> JSONResponse:
    """Return analysis chart data for a public scan result."""
    from src.analytics.soundcloud import aggregate_soundcloud_data
    from src.api.scan import _TASKS

    task = _TASKS.get(task_id)
    if not task or task.status != "done" or not task.result:
        return JSONResponse({"error": "scan not found"}, status_code=404)

    result = task.result
    artist_objects = result.get("_artist_objects", [])
    track_counts = result.get("_track_counts", {})

    data = aggregate_soundcloud_data(artist_objects, track_counts)
    data["taste_dna"] = result.get("taste_dna", {})
    data["character"] = result.get("character", {})
    data["matched_events"] = result.get("events", [])
    data["uncanny_headline"] = result.get("uncanny_headline", "")
    return JSONResponse(content=data)


# --- Scan-mode API adapters (mirror the auth endpoints for public scans) ---

def _get_scan_task(task_id: str):
    from src.api.scan import _TASKS
    task = _TASKS.get(task_id)
    if not task or task.status != "done" or not task.result:
        return None
    return task


@app.get("/api/taste-dna/scan/{task_id}")
async def get_scan_taste_dna(task_id: str) -> JSONResponse:
    """Public taste DNA for a scan result."""
    task = _get_scan_task(task_id)
    if not task:
        return JSONResponse({"error": "scan not found"}, status_code=404)
    return JSONResponse(content=task.result.get("taste_dna", {}))


@app.get("/api/events/scan/{task_id}")
async def get_scan_events(task_id: str) -> JSONResponse:
    """Public matched events for a scan result."""
    task = _get_scan_task(task_id)
    if not task:
        return JSONResponse({"error": "scan not found"}, status_code=404)
    events = task.result.get("top_5_matched_events", [])
    return JSONResponse(content={"matches": events, "total_events": len(events)})


@app.get("/api/taste/scan/{task_id}")
async def get_scan_taste(task_id: str) -> JSONResponse:
    """Public taste profile summary for a scan result."""
    task = _get_scan_task(task_id)
    if not task:
        return JSONResponse({"error": "scan not found"}, status_code=404)
    result = task.result
    character = result.get("character", {})
    return JSONResponse(content={
        "character": character,
        "uncanny_headline": result.get("uncanny_headline", ""),
        "stats": result.get("stats", {}),
    })


@app.get("/api/depth-score/scan/{task_id}")
async def get_scan_depth_score(task_id: str) -> JSONResponse:
    """Public depth score for a scan result.

    Returns error to keep the depth card hidden — depth score requires
    Spotify popularity data which isn't available from SoundCloud scraping.
    """
    return JSONResponse(content={"error": "depth score unavailable for SoundCloud-only scans"})


@app.get("/api/artists/scan/{task_id}")
async def get_scan_artists(task_id: str) -> JSONResponse:
    """Public artist list for a scan result."""
    task = _get_scan_task(task_id)
    if not task:
        return JSONResponse({"error": "scan not found"}, status_code=404)
    artists = task.result.get("_artist_objects", [])
    return JSONResponse(content={"artists": artists, "total": len(artists)})


@app.get("/api/scan/{task_id}/card.png")
async def get_scan_card(task_id: str) -> Response:
    """Generate and return the shareable taste card as a PNG image."""
    from src.cards.composer import compose_card

    task = _get_scan_task(task_id)
    if not task:
        return JSONResponse({"error": "scan not found"}, status_code=404)

    result = task.result
    character = result.get("character", {})
    taste_dna = result.get("taste_dna", {})
    events = result.get("events", [])
    top_event = events[0] if events else None

    img = compose_card(character, taste_dna=taste_dna, top_event=top_event)

    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    buf.seek(0)

    return Response(
        content=buf.getvalue(),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


# ---------------------------------------------------------------------------
# Routes: Analysis
# ---------------------------------------------------------------------------


@app.get("/analysis", response_class=HTMLResponse)
async def analysis_page(request: Request, user=Depends(get_session_user)) -> HTMLResponse:
    """Render the SoundCloud analysis page."""
    if not user:
        return RedirectResponse("/login")
    if not is_approved(user["id"]):
        return RedirectResponse("/pending")

    return templates.TemplateResponse(
        request,
        "analysis.html",
        {"user": user},
    )


@app.get("/api/analysis/soundcloud")
async def get_soundcloud_analysis(user=Depends(get_session_user)) -> JSONResponse:
    """Return aggregated SoundCloud analytics data."""
    from src.analytics.soundcloud import aggregate_soundcloud_data

    cache = _user_cache(user["id"]) if user else _cache
    artist_objects = cache.get("artist_objects", [])
    sc_track_counts = cache.get("sc_track_counts", {})
    sc_liked_events = cache.get("sc_liked_events", [])

    data = aggregate_soundcloud_data(artist_objects, sc_track_counts, sc_liked_events)
    return JSONResponse(content=data)


# ---------------------------------------------------------------------------
# Routes: PDF Export
# ---------------------------------------------------------------------------


@app.get("/api/report/pdf")
async def export_pdf() -> Response:
    """Generate a PDF report of taste profile and matching events."""
    from fpdf import FPDF

    events_data = _cache.get("events_snapshot", {})
    spotify_data = _cache.get("spotify_snapshot", {})
    soundcloud_data = _cache.get("soundcloud_snapshot", {})

    snapshot_matches = events_data.get("matches", [])
    all_events = events_data.get("events", [])
    sp_artists = spotify_data.get("artists", {})
    sc_artists = soundcloud_data.get("artists", {})
    avg_features = spotify_data.get("audio_features_estimated", {})

    # Genre counts
    genre_count: dict[str, int] = {}
    for a in sp_artists.values():
        for g in a.get("genres", []):
            genre_count[g] = genre_count.get(g, 0) + 1
    top_genres = sorted(genre_count.items(), key=lambda x: -x[1])[:15]

    # Vibe keyword matches
    vibe_keywords = {
        "techno", "hypertechno", "hard techno", "trance", "drum and bass",
        "house", "edm", "minimal", "hardstyle", "frenchcore", "hardcore",
        "tekno", "acid", "psytrance", "melodic", "gabber", "rave",
        "electronic", "bass", "dnb", "hard house",
    }
    matched_event_names = {m.get("event", "") for m in snapshot_matches}
    vibe_matches = []
    for ev in all_events:
        if ev["name"] in matched_event_names:
            continue
        combined = f"{ev['name']} {' '.join(ev.get('artists', []))}".lower()
        kw = [k for k in vibe_keywords if k in combined]
        if kw:
            vibe_matches.append({**ev, "keywords": kw})

    # ── Build PDF ──
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=20)

    # -- Colors --
    DARK = (15, 12, 25)
    CYAN = (0, 240, 255)
    MAGENTA = (255, 0, 170)
    WHITE = (255, 255, 255)
    MUTED = (160, 160, 180)
    CARD_BG = (25, 22, 40)
    GREEN = (0, 220, 130)
    AMBER = (255, 190, 50)

    def add_page_bg():
        pdf.set_fill_color(*DARK)
        pdf.rect(0, 0, 210, 297, "F")

    # ── Page 1: Title + Taste Profile ──
    pdf.add_page()
    add_page_bg()

    # Title
    pdf.set_text_color(*CYAN)
    pdf.set_font("Helvetica", "B", 32)
    pdf.cell(0, 20, "FREQUENZ", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*MUTED)
    pdf.set_font("Helvetica", "", 12)
    pdf.cell(0, 8, "Your Music Taste x Madrid Events Report", align="C", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", 9)
    generated = datetime.now().strftime("%B %d, %Y at %H:%M")
    pdf.cell(0, 6, f"Generated {generated}", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(8)

    # Stats bar
    total_artists = len(set(n.lower() for n in sp_artists) | set(n.lower() for n in sc_artists))
    pdf.set_fill_color(*CARD_BG)
    pdf.rect(15, pdf.get_y(), 180, 18, "F")
    y = pdf.get_y() + 4
    pdf.set_xy(20, y)
    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(55, 10, f"{total_artists} Artists")
    pdf.cell(55, 10, f"{len(all_events)} Events Scanned")
    pdf.cell(55, 10, f"{len(snapshot_matches) + len(vibe_matches)} Matches")
    pdf.ln(22)

    # Source breakdown
    pdf.set_text_color(*CYAN)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "YOUR LIBRARY", new_x="LMARGIN", new_y="NEXT")

    pdf.set_text_color(*WHITE)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(90, 7, f"Spotify: {len(sp_artists)} artists", new_x="RIGHT")
    pdf.cell(90, 7, f"SoundCloud: {len(sc_artists)} artists", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Top Genres - horizontal bars
    pdf.set_text_color(*CYAN)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "TOP GENRES", new_x="LMARGIN", new_y="NEXT")

    if top_genres:
        max_count = top_genres[0][1]
        for genre, count in top_genres:
            bar_width = (count / max_count) * 100
            y = pdf.get_y()

            # Genre name
            pdf.set_text_color(*MUTED)
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(50, 6, genre)

            # Bar
            ratio = count / max_count
            r = int(CYAN[0] + (MAGENTA[0] - CYAN[0]) * ratio)
            g = int(CYAN[1] + (MAGENTA[1] - CYAN[1]) * ratio)
            b = int(CYAN[2] + (MAGENTA[2] - CYAN[2]) * ratio)
            pdf.set_fill_color(r, g, b)
            pdf.rect(65, y + 1, bar_width, 4, "F")

            # Count
            pdf.set_xy(168, y)
            pdf.set_text_color(*WHITE)
            pdf.cell(20, 6, str(count), align="R")
            pdf.ln(6)

    # Audio DNA
    if avg_features:
        pdf.ln(4)
        pdf.set_text_color(*CYAN)
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, "AUDIO DNA", new_x="LMARGIN", new_y="NEXT")

        features_display = [
            ("Danceability", avg_features.get("danceability", 0)),
            ("Energy", avg_features.get("energy", 0)),
            ("Valence", avg_features.get("valence", 0)),
            ("Acousticness", avg_features.get("acousticness", 0)),
            ("Instrumentalness", avg_features.get("instrumentalness", 0)),
            ("Liveness", avg_features.get("liveness", 0)),
            ("Speechiness", avg_features.get("speechiness", 0)),
        ]
        for fname, fval in features_display:
            y = pdf.get_y()
            pdf.set_text_color(*MUTED)
            pdf.set_font("Helvetica", "", 9)
            pdf.cell(50, 6, fname)

            # Bar background
            pdf.set_fill_color(40, 35, 60)
            pdf.rect(65, y + 1, 100, 4, "F")

            # Bar fill
            pdf.set_fill_color(*CYAN)
            pdf.rect(65, y + 1, fval * 100, 4, "F")

            # Value
            pdf.set_xy(168, y)
            pdf.set_text_color(*WHITE)
            pdf.cell(20, 6, f"{fval:.0%}", align="R")
            pdf.ln(6)

        pdf.set_text_color(*MUTED)
        pdf.set_font("Helvetica", "", 9)
        pdf.cell(0, 6, f"Average Tempo: {avg_features.get('tempo', 0):.0f} BPM", new_x="LMARGIN", new_y="NEXT")

    # ── Page 2+: Matches ──
    pdf.add_page()
    add_page_bg()

    pdf.set_text_color(*CYAN)
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 14, "MATCHING EVENTS", align="C", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)

    # Exact matches
    if snapshot_matches:
        pdf.set_text_color(*GREEN)
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, f"EXACT MATCHES ({len(snapshot_matches)})", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        for m in sorted(snapshot_matches, key=lambda x: x.get("date", "zzz")):
            if pdf.get_y() > 260:
                pdf.add_page()
                add_page_bg()

            y = pdf.get_y()
            # Card background
            pdf.set_fill_color(*CARD_BG)
            pdf.rect(15, y, 180, 22, "F")

            # Green accent bar
            pdf.set_fill_color(*GREEN)
            pdf.rect(15, y, 2, 22, "F")

            pdf.set_xy(20, y + 2)

            # Date
            pdf.set_text_color(*MUTED)
            pdf.set_font("Helvetica", "", 9)
            date_str = m.get("date", "TBA")
            pdf.cell(22, 5, date_str)

            # Event name
            pdf.set_text_color(*WHITE)
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(110, 5, m.get("event", "")[:60])

            # Confidence
            pdf.set_text_color(*GREEN)
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(30, 5, f"{m.get('score', 100)}%", align="R")

            # Second line: artist + venue
            pdf.set_xy(42, y + 9)
            pdf.set_text_color(*CYAN)
            pdf.set_font("Helvetica", "B", 9)
            src = m.get("source", "spotify")
            src_label = "SC" if "soundcloud" in src else "SP"
            pdf.cell(60, 5, f"{m.get('your_artist', '')} [{src_label}]")

            pdf.set_text_color(*MUTED)
            pdf.set_font("Helvetica", "", 8)
            pdf.cell(80, 5, f"@ {m.get('venue', '')[:40]}")

            # Third line: URL
            url = m.get("url", "")
            if url:
                pdf.set_xy(42, y + 15)
                pdf.set_text_color(100, 100, 120)
                pdf.set_font("Helvetica", "", 7)
                pdf.cell(140, 5, url[:80])

            pdf.set_y(y + 24)

    # Vibe matches
    if vibe_matches:
        pdf.ln(4)
        if pdf.get_y() > 240:
            pdf.add_page()
            add_page_bg()

        pdf.set_text_color(*AMBER)
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 10, f"VIBE MATCHES ({len(vibe_matches)})", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)

        for ev in sorted(vibe_matches, key=lambda x: x.get("date", "zzz"))[:30]:
            if pdf.get_y() > 265:
                pdf.add_page()
                add_page_bg()

            y = pdf.get_y()
            pdf.set_fill_color(*CARD_BG)
            pdf.rect(15, y, 180, 16, "F")
            pdf.set_fill_color(*AMBER)
            pdf.rect(15, y, 2, 16, "F")

            pdf.set_xy(20, y + 2)

            # Date
            pdf.set_text_color(*MUTED)
            pdf.set_font("Helvetica", "", 9)
            date_str = ev.get("date", "")[:10] if ev.get("date") else "TBA"
            pdf.cell(22, 5, date_str)

            # Event
            pdf.set_text_color(*WHITE)
            pdf.set_font("Helvetica", "B", 9)
            pdf.cell(115, 5, ev.get("name", "")[:65])

            # Keywords
            pdf.set_text_color(*AMBER)
            pdf.set_font("Helvetica", "", 8)
            pdf.cell(30, 5, ", ".join(ev.get("keywords", [])[:2]), align="R")

            # Second line
            pdf.set_xy(42, y + 8)
            artists = ", ".join(ev.get("artists", [])[:3])
            pdf.set_text_color(*MUTED)
            pdf.set_font("Helvetica", "", 8)
            venue = ev.get("venue", "")
            line2 = f"{artists}" if artists else ""
            if venue:
                line2 += f"  @  {venue[:30]}"
            pdf.cell(140, 5, line2)

            pdf.set_y(y + 18)

    # Footer
    pdf.ln(10)
    pdf.set_text_color(80, 80, 100)
    pdf.set_font("Helvetica", "I", 8)
    pdf.cell(0, 5, f"Frequenz  |  {total_artists} artists  |  {len(all_events)} events scanned  |  Generated {generated}", align="C")

    # Output
    pdf_bytes = pdf.output()

    filename = f"frequenz-madrid-{datetime.now().strftime('%Y-%m-%d')}.pdf"
    return Response(
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


