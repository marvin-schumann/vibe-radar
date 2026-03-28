"""FastAPI web application for Vibe Radar."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from spotipy.cache_handler import CacheHandler

from src.api.auth import router as auth_router
from src.api.deps import get_session_user
from src.config import settings
from src.db.supabase import get_admin_client, is_approved, is_pro, upsert_connected_account
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
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Vibe Radar", version="1.0.0")

app.include_router(auth_router)

app.mount(
    "/static",
    StaticFiles(directory="src/web/static"),
    name="static",
)

templates = Jinja2Templates(directory="src/web/templates")


# ─────────────────────────────────────────
# Auth middleware: persist refreshed tokens
# ─────────────────────────────────────────


@app.middleware("http")
async def persist_refreshed_tokens(request: Request, call_next):
    request.state.new_tokens = None
    response = await call_next(request)
    if getattr(request.state, "new_tokens", None):
        access, refresh = request.state.new_tokens
        opts = dict(httponly=True, samesite="lax", secure=False)
        response.set_cookie("session_token", access, **opts)
        response.set_cookie("refresh_token", refresh, **opts)
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
        }
    return _user_caches[user_id]


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

    cache = _user_cache(user_id) if user_id else _cache

    if cache.get("refreshing"):
        logger.info("Pipeline already running for user {}", user_id)
        return

    cache["refreshing"] = True
    logger.info("Starting Vibe Radar pipeline (user={})", user_id or "anon")

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
            try:
                cache_handler = _SupabaseCacheHandler(user_id)
                spotify = SpotifyCollector.from_tokens(
                    access_token=spotify_acct["access_token"],
                    refresh_token=spotify_acct.get("refresh_token"),
                    cache_handler=cache_handler,
                )
                spotify_artists = await spotify.collect_artists()
                all_artists.extend(spotify_artists)
                logger.info("Spotify: {} artists for user {}", len(spotify_artists), user_id)
            except Exception as exc:
                logger.warning("Spotify collection failed for user {}: {}", user_id, exc)

        # SoundCloud
        sc_acct = acct_map.get("soundcloud")
        sc_username = sc_acct.get("username") if sc_acct else None
        if sc_username:
            try:
                soundcloud = SoundCloudCollector(username=sc_username)
                sc_artists = await soundcloud.collect_artists()
                all_artists.extend(sc_artists)
                logger.info("SoundCloud: {} artists for user {}", len(sc_artists), user_id)
            except Exception as exc:
                logger.warning("SoundCloud collection failed for user {}: {}", user_id, exc)
    else:
        # --- Anonymous / single-user: use local cache / env vars ---
        try:
            spotify = SpotifyCollector()
            spotify_artists = await spotify.collect_artists()
            all_artists.extend(spotify_artists)
            logger.info("Spotify: {} artists collected", len(spotify_artists))
        except Exception as exc:
            logger.warning("Spotify collection failed: {}", exc)

        try:
            if settings.soundcloud_username:
                soundcloud = SoundCloudCollector()
                sc_artists = await soundcloud.collect_artists()
                all_artists.extend(sc_artists)
                logger.info("SoundCloud: {} artists collected", len(sc_artists))
        except Exception as exc:
            logger.warning("SoundCloud collection failed: {}", exc)

    if not all_artists:
        logger.warning("No artists collected from any source")
        cache["taste_profile"] = TasteProfile()
        cache["matches"] = []
        cache["last_refresh"] = datetime.now(tz=timezone.utc).isoformat()
        cache["refreshing"] = False
        return

    # -- 2. Build taste profile --
    taste_profile = build_taste_profile(all_artists)
    cache["taste_profile"] = taste_profile

    # -- 3. Collect events from all sources --
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

    # -- 6. Combine and cache --
    all_matches = exact_matches + vibe_matches
    all_matches.sort(key=lambda m: m.sort_key)

    cache["matches"] = all_matches
    cache["last_refresh"] = datetime.now(tz=timezone.utc).isoformat()
    cache["refreshing"] = False

    logger.info(
        "Pipeline complete: {} total matches ({} exact, {} vibe)",
        len(all_matches),
        len(exact_matches),
        len(vibe_matches),
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
    # Auto-run pipeline in background if user has no cached data yet
    if not cache.get("last_refresh") and not cache.get("refreshing"):
        asyncio.create_task(_run_pipeline(user_id=user["id"]))

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
            "match_reason": f"{'Exact' if m.get('score', 0) >= 95 else 'Close'} match ({m.get('score', 0)}%): {m.get('your_artist', '')} on your {artist_source_tag.title()}",
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


@app.get("/api/refresh")
async def refresh_data(user=Depends(get_session_user)) -> JSONResponse:
    """Trigger a fresh data collection + matching run for the current user."""
    if not user:
        return JSONResponse({"status": "error", "message": "Not authenticated"}, status_code=401)

    cache = _user_cache(user["id"])
    if cache.get("refreshing"):
        return JSONResponse(
            content={"status": "already_running", "message": "A refresh is already in progress."},
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
    pdf.cell(0, 20, "VIBE RADAR", align="C", new_x="LMARGIN", new_y="NEXT")

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
    pdf.cell(0, 5, f"Vibe Radar  |  {total_artists} artists  |  {len(all_events)} events scanned  |  Generated {generated}", align="C")

    # Output
    pdf_bytes = pdf.output()

    filename = f"vibe-radar-madrid-{datetime.now().strftime('%Y-%m-%d')}.pdf"
    return Response(
        content=bytes(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


