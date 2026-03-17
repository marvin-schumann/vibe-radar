"""FastAPI web application for Vibe Radar."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from src.config import settings
from src.models import Match, MatchType, TasteProfile

DATA_DIR = Path(__file__).parent.parent.parent / "data"

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
    "events": [],
    "last_refresh": None,
    "refreshing": False,
}


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


async def _run_pipeline() -> None:
    """Run the full data collection and matching pipeline, updating the cache."""
    from src.collectors.events.bandsintown import BandsintownCollector
    from src.collectors.events.resident_advisor import ResidentAdvisorCollector
    from src.collectors.events.songkick import SongkickCollector
    from src.collectors.soundcloud import SoundCloudCollector
    from src.collectors.spotify import SpotifyCollector
    from src.matching.exact import ExactMatcher
    from src.matching.vibe import VibeMatcher, build_taste_profile

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
    # Try live profile first, fall back to snapshot
    profile = _cache.get("taste_profile")
    if profile is not None:
        return JSONResponse(
            content={
                "taste_profile": _serialize_taste_profile(profile),
                "last_refresh": _cache.get("last_refresh"),
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
    # Try live matches first
    live_matches: list[Match] = _cache.get("matches", [])
    if live_matches:
        filtered = live_matches
        if match_type and match_type != "all":
            try:
                mt = MatchType(match_type)
                filtered = [m for m in filtered if m.match_type == mt]
            except ValueError:
                pass
        return JSONResponse(
            content={
                "matches": [_serialize_match(m) for m in filtered],
                "total": len(filtered),
                "match_type": match_type or "all",
                "last_refresh": _cache.get("last_refresh"),
            }
        )

    # Fall back to snapshot data
    events_data = _cache.get("events_snapshot", {})
    snapshot_matches = events_data.get("matches", [])
    all_events = events_data.get("events", [])

    results = []
    # Serve exact matches from snapshot
    for m in snapshot_matches:
        entry = {
            "event": {
                "name": m.get("event", ""),
                "date": m.get("date", ""),
                "url": m.get("url", ""),
                "image_url": None,
                "source": m.get("source", "").lower().replace(" ", "_"),
                "artists": [m.get("event_artist", "")],
                "venue": {"name": m.get("venue", ""), "city": "Madrid", "address": None},
                "price": None,
                "description": None,
            },
            "matched_artist": {
                "name": m.get("your_artist", ""),
                "source": "spotify",
                "image_url": None,
                "genres": [],
            },
            "event_artist_name": m.get("event_artist", ""),
            "match_type": "exact" if m.get("score", 0) >= 95 else "vibe",
            "confidence": m.get("score", 0) / 100.0,
            "match_reason": f"{'Exact' if m.get('score', 0) >= 95 else 'Close'} match ({m.get('score', 0)}%)",
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
