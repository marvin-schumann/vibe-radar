"""FastAPI web application for Vibe Radar."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from src.collectors.events.bandsintown import BandsintownCollector
from src.collectors.events.resident_advisor import ResidentAdvisorCollector
from src.collectors.events.songkick import SongkickCollector
from src.collectors.soundcloud import SoundCloudCollector
from src.collectors.spotify import SpotifyCollector
from src.config import settings
from src.matching.exact import ExactMatcher
from src.matching.vibe import VibeMatcher, build_taste_profile
from src.models import Match, MatchType, TasteProfile

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="Vibe Radar", version="1.0.0")

app.mount(
    "/static",
    StaticFiles(directory="src/web/static"),
    name="static",
)

templates = Jinja2Templates(directory="src/web/templates")

# ---------------------------------------------------------------------------
# In-memory cache
# ---------------------------------------------------------------------------

_cache: dict[str, Any] = {
    "taste_profile": None,
    "matches": [],
    "last_refresh": None,
    "refreshing": False,
}


# ---------------------------------------------------------------------------
# Pipeline logic
# ---------------------------------------------------------------------------


async def _run_pipeline() -> None:
    """Run the full data collection and matching pipeline, updating the cache."""
    logger.info("Starting Vibe Radar pipeline refresh...")

    # -- 1. Collect user artists from music sources --
    all_artists = []

    # Spotify
    try:
        spotify = SpotifyCollector()
        spotify_artists = await spotify.collect_artists()
        all_artists.extend(spotify_artists)
        logger.info("Spotify: {} artists collected", len(spotify_artists))
    except Exception as exc:
        logger.warning("Spotify collection failed: {}", exc)

    # SoundCloud
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
        _cache["taste_profile"] = TasteProfile()
        _cache["matches"] = []
        _cache["last_refresh"] = datetime.now(tz=timezone.utc).isoformat()
        _cache["refreshing"] = False
        return

    # -- 2. Build taste profile --
    taste_profile = build_taste_profile(all_artists)
    _cache["taste_profile"] = taste_profile

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

    _cache["matches"] = all_matches
    _cache["last_refresh"] = datetime.now(tz=timezone.utc).isoformat()
    _cache["refreshing"] = False

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
async def dashboard(request: Request) -> HTMLResponse:
    """Render the main dashboard page."""
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "city": settings.city,
            "last_refresh": _cache.get("last_refresh"),
        },
    )


# ---------------------------------------------------------------------------
# Routes: API
# ---------------------------------------------------------------------------


@app.get("/api/taste")
async def get_taste_profile() -> JSONResponse:
    """Return the user's taste profile as JSON."""
    profile = _cache.get("taste_profile")
    return JSONResponse(
        content={
            "taste_profile": _serialize_taste_profile(profile),
            "last_refresh": _cache.get("last_refresh"),
        }
    )


@app.get("/api/events")
async def get_events(
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
    matches: list[Match] = _cache.get("matches", [])

    # Filter by match type
    if match_type and match_type != "all":
        try:
            mt = MatchType(match_type)
            matches = [m for m in matches if m.match_type == mt]
        except ValueError:
            pass

    # Filter by days ahead
    effective_days = days_ahead if days_ahead is not None else settings.days_ahead
    cutoff = datetime.now(tz=timezone.utc)
    from datetime import timedelta

    deadline = cutoff + timedelta(days=effective_days)
    matches = [
        m for m in matches
        if m.event.date.tzinfo is not None and m.event.date <= deadline
    ]

    return JSONResponse(
        content={
            "matches": [_serialize_match(m) for m in matches],
            "total": len(matches),
            "match_type": match_type or "all",
            "last_refresh": _cache.get("last_refresh"),
        }
    )


@app.get("/api/refresh")
async def refresh_data() -> JSONResponse:
    """Trigger a fresh data collection + matching run."""
    if _cache.get("refreshing"):
        return JSONResponse(
            content={
                "status": "already_running",
                "message": "A refresh is already in progress.",
            },
            status_code=409,
        )

    _cache["refreshing"] = True

    try:
        await _run_pipeline()
        return JSONResponse(
            content={
                "status": "ok",
                "message": "Pipeline refresh completed.",
                "last_refresh": _cache.get("last_refresh"),
            }
        )
    except Exception as exc:
        _cache["refreshing"] = False
        logger.error("Pipeline refresh failed: {}", exc)
        return JSONResponse(
            content={
                "status": "error",
                "message": f"Pipeline refresh failed: {exc}",
            },
            status_code=500,
        )


# ---------------------------------------------------------------------------
# Routes: Spotify OAuth
# ---------------------------------------------------------------------------


@app.get("/auth/spotify")
async def spotify_auth() -> RedirectResponse:
    """Redirect the user to Spotify's authorization page."""
    from spotipy.oauth2 import SpotifyOAuth

    auth_manager = SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.spotify_redirect_uri,
        scope=" ".join([
            "user-top-read",
            "user-follow-read",
            "user-read-recently-played",
            "user-library-read",
        ]),
    )
    auth_url = auth_manager.get_authorize_url()
    return RedirectResponse(url=auth_url)


@app.get("/auth/spotify/callback")
async def spotify_callback(code: str = Query(...)) -> RedirectResponse:
    """Handle the Spotify OAuth callback and store the token."""
    from spotipy.oauth2 import SpotifyOAuth

    from src.collectors.spotify import TOKEN_CACHE_PATH, SCOPES

    auth_manager = SpotifyOAuth(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        redirect_uri=settings.spotify_redirect_uri,
        scope=SCOPES,
        cache_path=str(TOKEN_CACHE_PATH),
    )

    try:
        auth_manager.get_access_token(code)
        logger.info("Spotify OAuth token obtained and cached")
    except Exception as exc:
        logger.error("Spotify OAuth callback failed: {}", exc)
        return RedirectResponse(url="/?auth_error=1")

    return RedirectResponse(url="/?auth_success=1")
