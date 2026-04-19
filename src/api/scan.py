"""Public /api/scan endpoint — 30-second taste-DNA demo, no auth required.

DESIGN
======

This router powers the anonymous "paste a SoundCloud URL → see your taste DNA
+ character + first matched event in under 30 seconds" flow on the landing
page. It intentionally bypasses the authenticated dashboard pipeline so a
first-time visitor can get a wow-moment BEFORE any signup or email gate.

Flow:
    1. POST /api/scan  {soundcloud_url}  →  {task_id, status}
    2. GET  /api/scan/{task_id}           →  progress while running
    3. GET  /api/scan/{task_id}           →  final result when done

Why an async task + polling (not a single blocking POST)?
    SoundCloud scraping + event collection + matching takes 10-30 s and
    depends on external APIs that can stall. A single blocking POST would
    hold an HTTP connection open for the full duration, break on flaky
    mobile networks, and hide progress from the user. Polling lets the
    landing page show a live "scanning 412 likes…" counter — which *is*
    the demo moment we're selling.

State storage:
    In-process ``_TASKS`` dict keyed by task_id (uuid4). This is fine for
    a single-worker deploy; if/when Frequenz scales to multiple Uvicorn
    workers behind a load balancer, move this to Redis (key: task_id,
    value: JSON, TTL: 1 h). Swap is localised to this file.

Rate limiting:
    Same per-IP cooldown pattern as ``/api/waitlist`` (60 s) — prevents a
    single IP from hammering SoundCloud via our backend. Polling GETs are
    NOT rate-limited because the frontend will poll every 1-2 s.

Security / privacy:
    - No DB writes. No email collected. No cookies.
    - The SoundCloud URL is held only in-memory for the task lifetime.
    - Tasks auto-expire after ``_TASK_TTL_SECONDS`` (1 h) via lazy GC.
    - No user-supplied string is logged at INFO level other than the
      sanitised ``username`` field extracted from the URL.

Failure modes (all return status=failed with a user-friendly error):
    - Malformed or non-soundcloud.com URL → 400 at POST time
    - Private / non-existent profile → task completes with error
    - SoundCloud client_id extraction blocked → task completes with error
    - Zero artists collected → task completes with a "profile looks empty"
    - Zero events in the user's city → result still returned, events=[]
"""

from __future__ import annotations

import asyncio
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from src.config import settings

router = APIRouter(prefix="/api", tags=["scan"])


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_SCAN_COOLDOWN_SECONDS = 60          # per-IP cooldown for POST /api/scan
_TASK_TTL_SECONDS = 60 * 60          # 1 h — after this, tasks are GC'd
_MAX_TASKS_IN_MEMORY = 5_000         # hard cap to prevent unbounded growth
_POLL_STALE_SECONDS = 120            # a running task that hasn't progressed

# Accepted URL forms:
#   https://soundcloud.com/username
#   https://soundcloud.com/username/
#   http://soundcloud.com/username
#   https://www.soundcloud.com/username
#   https://m.soundcloud.com/username
_SC_URL_PATTERN = re.compile(
    r"^https?://(?:www\.|m\.)?soundcloud\.com/([a-zA-Z0-9][a-zA-Z0-9_.\-]{1,49})/?$"
)


# ---------------------------------------------------------------------------
# In-process task state
# ---------------------------------------------------------------------------


@dataclass
class ScanProgress:
    """Live counters shown to the user while a scan is running."""

    current_step: str = "queued"            # queued | scraping_likes | scraping_follows | computing_taste | matching_events | done | failed
    likes_scanned: int = 0
    reposts_scanned: int = 0
    follows_scanned: int = 0
    unique_artists: int = 0
    events_scanned: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_step": self.current_step,
            "likes_scanned": self.likes_scanned,
            "reposts_scanned": self.reposts_scanned,
            "follows_scanned": self.follows_scanned,
            "unique_artists": self.unique_artists,
            "events_scanned": self.events_scanned,
        }


@dataclass
class ScanTask:
    """In-memory record for a running / finished scan."""

    task_id: str
    soundcloud_url: str
    username: str
    status: str = "queued"                  # queued | scraping | computing | matching | done | failed
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    progress: ScanProgress = field(default_factory=ScanProgress)
    result: dict[str, Any] | None = None
    error: str | None = None

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "task_id": self.task_id,
            "status": self.status,
            "progress": self.progress.to_dict(),
        }
        if self.result is not None:
            d["result"] = self.result
        if self.error is not None:
            d["error"] = self.error
        return d


# task_id → ScanTask
_TASKS: dict[str, ScanTask] = {}
# Per-IP rate-limit tracker (same pattern as _waitlist_last_call in main.py)
_scan_last_call: dict[str, float] = {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _client_ip(request: Request) -> str:
    """Best-effort client IP for rate limiting only — never logged or stored."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_scan(ip: str) -> bool:
    """Return True if the call is allowed, False if rate-limited."""
    now = time.time()
    last = _scan_last_call.get(ip, 0.0)
    if now - last < _SCAN_COOLDOWN_SECONDS:
        return False
    _scan_last_call[ip] = now
    return True


def _parse_soundcloud_url(raw: str) -> tuple[str, str] | None:
    """Validate a SoundCloud URL and return (canonical_url, username).

    Returns None for anything that isn't an obvious profile URL. We only
    accept top-level user profiles — not /tracks/, /sets/, etc. — because
    the taste-DNA flow needs the user's library, not a single track.
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    if len(raw) > 300:
        return None

    # Allow bare "soundcloud.com/foo" without scheme
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    try:
        parsed = urlparse(raw)
    except Exception:
        return None

    host = (parsed.netloc or "").lower()
    if host not in {"soundcloud.com", "www.soundcloud.com", "m.soundcloud.com"}:
        return None

    match = _SC_URL_PATTERN.match(raw)
    if not match:
        return None

    username = match.group(1)
    # Reject obvious non-user paths
    if username in {"discover", "stream", "you", "search", "charts", "tags", "upload"}:
        return None

    canonical = f"https://soundcloud.com/{username}"
    return canonical, username


def _gc_old_tasks() -> None:
    """Evict tasks older than TTL. Called opportunistically on each POST."""
    now = time.time()
    expired = [
        tid for tid, task in _TASKS.items()
        if now - task.updated_at > _TASK_TTL_SECONDS
    ]
    for tid in expired:
        _TASKS.pop(tid, None)
    if expired:
        logger.debug("scan: evicted {} expired tasks", len(expired))

    # Hard cap fallback — drop oldest if we ever get flooded
    if len(_TASKS) > _MAX_TASKS_IN_MEMORY:
        sorted_tasks = sorted(_TASKS.items(), key=lambda kv: kv[1].updated_at)
        drop = len(_TASKS) - _MAX_TASKS_IN_MEMORY
        for tid, _ in sorted_tasks[:drop]:
            _TASKS.pop(tid, None)
        logger.warning("scan: hard cap hit — dropped {} oldest tasks", drop)

    # Prune stale rate-limit entries (older than 2× cooldown)
    cutoff = now - _SCAN_COOLDOWN_SECONDS * 2
    stale_ips = [ip for ip, ts in _scan_last_call.items() if ts < cutoff]
    for ip in stale_ips:
        _scan_last_call.pop(ip, None)


# ---------------------------------------------------------------------------
# Character mapping (tribe → full persona for the landing-page card)
# ---------------------------------------------------------------------------

# The 10 launch characters per the research session of 2026-04-10. Each tribe
# from compute_taste_dna() maps to ONE character. Each character has:
#   - name: English alliterative canonical name (for press, captions, sharing)
#   - alt_name: scene-language secondary name (in-joke for the heads)
#   - voice_line: Duolingo-style one-sentence personality line
#   - image_path: path under /static/characters/<slug>.png to serve to the frontend
#   - rarity: common / uncommon / rare / legendary (drives the card border colour)
#
# The 7 base tribes from src/analytics/taste_dna.py map directly. Hard Rhino,
# Garage Swan, and Boom-Bap Owl are 3 hybrid characters used as override targets
# when the user's secondary genre signal is strong (handled below in
# _override_character_for_secondary_signal).
_CHARACTERS: dict[str, dict[str, Any]] = {
    "bunker_bear": {
        "name": "Bunker Bear",
        "alt_name": "Betonhund",
        "voice_line": "Bunker Bear hasn't seen sunlight since last weekend.",
        "image_path": "/static/characters/bunker_bear.png",
        "rarity": "uncommon",
    },
    "fog_whale": {
        "name": "Fog Whale",
        "alt_name": "Nebelwal",
        "voice_line": "Fog Whale is somewhere underneath the bassline, listening.",
        "image_path": "/static/characters/fog_whale.png",
        "rarity": "rare",
    },
    "sunrise_stag": {
        "name": "Sunrise Stag",
        "alt_name": "Morgenrothirsch",
        "voice_line": "Sunrise Stag is still listening to the same set from 6am.",
        "image_path": "/static/characters/sunrise_stag.png",
        "rarity": "uncommon",
    },
    "disco_flamingo": {
        "name": "Disco Flamingo",
        "alt_name": "Espejo Rosa",
        "voice_line": "Disco Flamingo never left the dancefloor.",
        "image_path": "/static/characters/disco_flamingo.png",
        "rarity": "common",
    },
    "lounge_lynx": {
        "name": "Lounge Lynx",
        "alt_name": "Sofakatze",
        "voice_line": "Lounge Lynx found that record before Discogs existed.",
        "image_path": "/static/characters/lounge_lynx.png",
        "rarity": "common",
    },
    "hard_rhino": {
        "name": "Hard Rhino",
        "alt_name": "Stahlhorn",
        "voice_line": "Hard Rhino remembers the BPM of every set he ever played.",
        "image_path": "/static/characters/hard_rhino.png",
        "rarity": "rare",
    },
    "breakbeat_falcon": {
        "name": "Breakbeat Falcon",
        "alt_name": "Amen Falke",
        "voice_line": "Breakbeat Falcon clocks every snare from across the room.",
        "image_path": "/static/characters/breakbeat_falcon.png",
        "rarity": "uncommon",
    },
    "jungle_tiger": {
        "name": "Jungle Tiger",
        "alt_name": "Selva",
        "voice_line": "Jungle Tiger has a sound system bigger than your apartment.",
        "image_path": "/static/characters/jungle_tiger.png",
        "rarity": "uncommon",
    },
    "garage_swan": {
        "name": "Garage Swan",
        "alt_name": "Eleganza",
        "voice_line": "Garage Swan only goes out in white.",
        "image_path": "/static/characters/garage_swan.png",
        "rarity": "rare",
    },
    "boom_bap_owl": {
        "name": "Circuit Owl",
        "alt_name": "Patchwerk",
        "voice_line": "Circuit Owl knows what oscillator made that sound.",
        "image_path": "/static/characters/boom_bap_owl.png",
        "rarity": "uncommon",
    },
}

# Maps the compute_taste_dna() tribe name → the canonical character slug.
# Keep this in sync with TRIBES in src/analytics/taste_dna.py.
_TRIBE_TO_CHARACTER: dict[str, str] = {
    "Warehouse Monk": "bunker_bear",
    "Sonic Archaeologist": "lounge_lynx",
    "Fog Machine Philosopher": "fog_whale",
    "Strobe Nomad": "disco_flamingo",
    "Dawn Chaser": "sunrise_stag",
    "Bass Templar": "breakbeat_falcon",
    "Circuit Bender": "boom_bap_owl",
}


def _override_character_for_secondary_signal(
    base_slug: str, taste_dna: dict[str, Any]
) -> str:
    """Apply genre-specific overrides on top of the base tribe → character mapping.

    Some characters are 'hybrid forms' that override the base tribe when a
    user's secondary genre signal is strong:
    - Hard Rhino overrides Warehouse Monk if hard-techno share is dominant
    - Jungle Tiger overrides Bass Templar if jungle/ragga share is dominant
    - Garage Swan overrides Bass Templar if UK garage share is dominant

    All hybrid logic is contained here so the base mapping stays simple.
    """
    families = (taste_dna.get("taste_dna") or {}).get("genre_families") or {}
    if not families:
        return base_slug

    # Hard techno override (Bass Templar -> Breakbeat Falcon stays default;
    # Warehouse Monk -> Hard Rhino if the techno share is heavy AND industrial-leaning)
    if base_slug == "bunker_bear":
        techno_share = families.get("techno", 0)
        if techno_share > 0.65:
            # Heavy techno listener with industrial lean → Hard Rhino
            return "hard_rhino"

    # Bass Templar override - check for jungle vs garage vs DnB
    if base_slug == "breakbeat_falcon":
        bass_share = families.get("bass", 0)
        # Use the dominant artist's specific genre as the tiebreaker
        # (this is a heuristic, refined post-launch)
        return base_slug  # default to falcon for now; jungle_tiger / garage_swan
        # are reachable via direct tribe-naming once we add them as proper
        # tribes in taste_dna.py

    return base_slug


def _derive_character(taste_dna: dict[str, Any]) -> dict[str, Any] | None:
    """Extract the full character object from a taste_dna result.

    Returns a dict with: slug, name, alt_name, voice_line, image_path, rarity,
    tagline, description, icon, confidence. The slug + image_path are what
    the frontend uses to render the character image; the name + voice_line are
    the headline copy on the card.
    """
    tribe_block = taste_dna.get("taste_tribe") or {}
    tribe = tribe_block.get("tribe")
    if not tribe:
        return None

    tribe_name = tribe.get("name")
    base_slug = _TRIBE_TO_CHARACTER.get(tribe_name)
    if not base_slug:
        # Unknown tribe — fall back to name-only character with no image
        return {
            "slug": None,
            "name": tribe_name,
            "alt_name": None,
            "voice_line": None,
            "image_path": None,
            "rarity": "common",
            "tagline": tribe.get("tagline"),
            "description": tribe.get("description"),
            "icon": tribe.get("icon"),
            "confidence": tribe.get("confidence"),
        }

    final_slug = _override_character_for_secondary_signal(base_slug, taste_dna)
    char = _CHARACTERS[final_slug]
    return {
        "slug": final_slug,
        "name": char["name"],
        "alt_name": char["alt_name"],
        "voice_line": char["voice_line"],
        "image_path": char["image_path"],
        "rarity": char["rarity"],
        "tagline": tribe.get("tagline"),
        "description": tribe.get("description"),
        "icon": tribe.get("icon"),
        "confidence": tribe.get("confidence"),
    }


def _derive_uncanny_insight(
    artists: list[dict[str, Any]],
    track_counts: dict[str, int],
    taste_dna: dict[str, Any],
) -> str | None:
    """Generate a single surprising one-liner about the user's library.

    Strategy (first hit wins):
      1. Rarest cross-genre bridge → "You bridge X + Y, only Z% of listeners do"
      2. Top-scene-city over-index → "42% of your likes are Berlin-scene"
      3. Most-liked single artist → "You've liked N tracks from {artist}"
    """
    # 1. Rarest bridge
    bridges = (taste_dna.get("cross_genre_bridges") or {}).get("bridges") or []
    if bridges:
        rarest = bridges[0]
        return (
            f"You bridge {rarest['genre_a']} + {rarest['genre_b']} — "
            f"only ~{rarest['rarity_pct']}% of electronic listeners do."
        )

    # 2. Scene-city over-index
    cities = (taste_dna.get("scene_city") or {}).get("cities") or []
    if cities and cities[0].get("percentage", 0) >= 25:
        top = cities[0]
        return (
            f"{top['percentage']}% of your library lives in the {top['city']} scene."
        )

    # 3. Most-liked single artist
    if track_counts:
        top_key = max(track_counts.items(), key=lambda kv: kv[1])
        top_name_key, top_count = top_key
        if top_count >= 3:
            # Look up the display name (normalized → display)
            display = top_name_key
            for a in artists:
                if (a.get("name") or "").lower().strip() == top_name_key:
                    display = a["name"]
                    break
            return f"You've liked {top_count} tracks from {display}."

    # 4. Dancefloor ratio (fallback)
    df = taste_dna.get("dancefloor_ratio") or {}
    df_pct = df.get("dancefloor_pct", 0)
    if df_pct >= 80:
        return f"{df_pct}% of your library is pure dancefloor energy."
    elif df_pct <= 30:
        return f"Only {df_pct}% dancefloor — you're a headphones-first listener."

    # 5. Tribe-based generic (ultimate fallback — never returns None)
    tribe = (taste_dna.get("taste_tribe") or {}).get("tribe") or {}
    tribe_name = tribe.get("name", "")
    if tribe_name:
        return f"Your listening pattern marks you as a {tribe_name}."

    return "Your taste is uncanny."


# ---------------------------------------------------------------------------
# Background worker — runs the full scrape → taste DNA → match pipeline
# ---------------------------------------------------------------------------


async def _run_scan(task_id: str) -> None:
    """Run the full scan pipeline for a given task_id.

    This is launched via ``asyncio.create_task`` from the POST handler and
    updates the in-memory ``ScanTask`` record as it progresses. It never
    raises — any failure is recorded on the task itself.
    """
    # Imports are local to keep module import cost tiny and avoid circular
    # imports with src.api.main (which imports this router).
    from src.analytics.taste_dna import compute_taste_dna
    from src.collectors.soundcloud import SoundCloudCollector

    task = _TASKS.get(task_id)
    if task is None:
        logger.warning("scan: task {} disappeared before start", task_id)
        return

    username_log = task.username  # safe: regex-validated
    logger.info("scan: starting task {} for @{}", task_id, username_log)

    # --- 1. Scrape SoundCloud -------------------------------------------------
    task.status = "scraping"
    task.progress.current_step = "scraping_likes"
    task.touch()

    try:
        collector = SoundCloudCollector(username=task.username)
        sc_artists = await collector.collect_artists()
    except ValueError as exc:
        # Raised if username is blank — shouldn't happen after validation
        _fail(task, f"invalid soundcloud username: {exc}")
        return
    except Exception as exc:
        logger.warning("scan {}: SoundCloud scrape failed: {}", task_id, exc)
        _fail(
            task,
            "could not reach soundcloud — the profile might be private, "
            "or soundcloud is blocking us right now. try again in a minute.",
        )
        return

    if not sc_artists:
        _fail(
            task,
            "this profile doesn't seem to have any liked tracks yet. "
            "like a few tracks on soundcloud and try again.",
        )
        return

    # Dedupe + build the artist_objects format compute_taste_dna expects
    seen: dict[str, dict[str, Any]] = {}
    for a in sc_artists:
        key = a.normalized_name
        if not key or key in seen:
            continue
        seen[key] = {
            "name": a.name,
            "source": a.source.value,
            "image_url": a.image_url,
            "genres": a.genres,
            "popularity": a.popularity,
            "play_count": a.play_count,
        }
    artist_objects = list(seen.values())

    task.progress.likes_scanned = len(sc_artists)
    task.progress.unique_artists = len(artist_objects)
    task.progress.current_step = "computing_taste"
    task.touch()
    logger.info(
        "scan {}: scraped {} artists ({} unique)",
        task_id, len(sc_artists), len(artist_objects),
    )

    # --- 2. Compute taste DNA -------------------------------------------------
    task.status = "computing"
    try:
        taste_dna = compute_taste_dna(artist_objects)
    except Exception as exc:
        logger.exception("scan {}: taste_dna failed: {}", task_id, exc)
        _fail(task, "failed to compute taste dna — please try again.")
        return

    character = _derive_character(taste_dna)
    insight = _derive_uncanny_insight(
        artist_objects, collector.track_counts, taste_dna,
    )

    # --- 3. Match events ------------------------------------------------------
    task.progress.current_step = "matching_events"
    task.status = "matching"
    task.touch()

    try:
        top_events = await _match_top_events(sc_artists, task)
    except Exception as exc:
        # Non-fatal — we still return taste DNA + character even if matching fails
        logger.warning("scan {}: event matching failed: {}", task_id, exc)
        top_events = []

    # --- 4. Done --------------------------------------------------------------
    # Build result in TWO formats:
    #   - full: the detailed internal representation (for debugging / future API)
    #   - top-level fields flattened for the landing page JS (renderReveal)
    flat_events = []
    for m in top_events:
        ev = m.get("event", {})
        venue = ev.get("venue") or {}
        flat_events.append({
            "name": ev.get("name", ""),
            "venue": venue.get("name", ""),
            "date": ev.get("date", ""),
            "match_score": round(m.get("confidence", 0) * 100),
            "url": ev.get("url", ""),
            "blurred": False,
        })

    task.result = {
        # Flat fields the landing page JS expects
        "uncanny_headline": insight or "Your taste is uncanny.",
        "character_name": character.get("name", ""),
        "character_alt": character.get("alt_name", ""),
        "voice_line": character.get("voice_line", ""),
        "character_image": character.get("image_path", ""),
        "events": flat_events,
        # Full structured data (for API consumers, Monday Drop, cards)
        "taste_dna": taste_dna,
        "character": character,
        "top_5_matched_events": top_events,
        "one_uncanny_insight": insight,
        "stats": {
            "unique_artists": len(artist_objects),
            "likes_scanned": len(sc_artists),
            "events_scanned": task.progress.events_scanned,
            "city": settings.city,
        },
        # Raw data for the reveal page (analysis charts)
        "_artist_objects": artist_objects,
        "_track_counts": dict(collector.track_counts),
    }
    task.status = "done"
    task.progress.current_step = "done"
    task.touch()
    logger.info(
        "scan {}: done — {} artists, {} events matched",
        task_id, len(artist_objects), len(top_events),
    )


async def _match_top_events(
    sc_artists: list[Any],  # list[Artist] but local import avoidance
    task: ScanTask,
) -> list[dict[str, Any]]:
    """Run the full exact + vibe matching pipeline and return top 5 as dicts.

    Collects events from the same three sources as the authenticated
    pipeline (RA, Bandsintown, Songkick), runs ExactMatcher + VibeMatcher,
    and returns the top 5 serialised matches. Returns [] if no events are
    available for the configured city.
    """
    from src.collectors.events.bandsintown import BandsintownCollector
    from src.collectors.events.resident_advisor import ResidentAdvisorCollector
    from src.collectors.events.songkick import SongkickCollector
    from src.matching.exact import ExactMatcher
    from src.matching.vibe import VibeMatcher, build_taste_profile

    days = settings.days_ahead
    artist_names = [a.name for a in sc_artists]

    ra = ResidentAdvisorCollector()
    bit = BandsintownCollector()
    sk = SongkickCollector()

    results = await asyncio.gather(
        ra.collect_events(days_ahead=days),
        bit.collect_events(artist_names=artist_names, days_ahead=days),
        sk.collect_events(days_ahead=days),
        return_exceptions=True,
    )

    all_events: list[Any] = []
    for source_name, result in zip(
        ["Resident Advisor", "Bandsintown", "Songkick"], results
    ):
        if isinstance(result, Exception):
            logger.warning("scan {}: {} failed: {}", task.task_id, source_name, result)
            continue
        all_events.extend(result)

    task.progress.events_scanned = len(all_events)
    task.touch()

    if not all_events:
        return []

    taste_profile = build_taste_profile(sc_artists)

    exact = ExactMatcher().match(sc_artists, all_events)
    matched_urls = {m.event.url for m in exact if m.event.url}

    vibe = VibeMatcher().match(
        sc_artists,
        all_events,
        taste_profile=taste_profile,
        exclude_event_ids=matched_urls,
    )

    combined = exact + vibe
    combined.sort(key=lambda m: (-m.confidence, m.event.date))
    top = combined[:5]

    return [_serialise_match(m) for m in top]


def _serialise_match(match: Any) -> dict[str, Any]:
    """Tiny local serializer — mirrors _serialize_match in main.py.

    We duplicate this rather than import from main.py to avoid creating
    a circular import (main.py imports this router).
    """
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


def _fail(task: ScanTask, error: str) -> None:
    """Mark a task as failed and record the error."""
    task.status = "failed"
    task.error = error
    task.progress.current_step = "failed"
    task.touch()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/scan")
async def start_scan(request: Request) -> JSONResponse:
    """Kick off an async SoundCloud taste-DNA scan.

    Public endpoint — no auth, no cookies, no DB writes. Rate-limited per
    IP at ``_SCAN_COOLDOWN_SECONDS``. Returns a ``task_id`` the client
    then polls via ``GET /api/scan/{task_id}``.

    Request body:
        {"soundcloud_url": "https://soundcloud.com/username"}

    Responses:
        200: {"task_id": "...", "status": "queued"}
        400: invalid / malformed URL
        429: rate-limited
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    raw_url = body.get("soundcloud_url") if isinstance(body, dict) else None
    parsed = _parse_soundcloud_url(raw_url or "")
    if parsed is None:
        return JSONResponse(
            {
                "error": (
                    "invalid soundcloud url — expected something like "
                    "https://soundcloud.com/yourname"
                )
            },
            status_code=400,
        )

    # Rate-limit AFTER validation so typos don't burn the cooldown
    if not _rate_limit_scan(_client_ip(request)):
        return JSONResponse(
            {"error": "rate limited — please wait a moment before retrying"},
            status_code=429,
        )

    canonical_url, username = parsed

    _gc_old_tasks()

    task_id = uuid.uuid4().hex
    task = ScanTask(
        task_id=task_id,
        soundcloud_url=canonical_url,
        username=username,
    )
    _TASKS[task_id] = task

    # Fire-and-forget background task. We deliberately use asyncio.create_task
    # (not FastAPI BackgroundTasks) so the task survives response-send and we
    # can update shared state while the client polls.
    asyncio.create_task(_run_scan(task_id))

    logger.info("scan: queued task {} for @{}", task_id, username)
    return JSONResponse({"task_id": task_id, "status": "queued"})


@router.get("/scan/{task_id}")
async def get_scan(task_id: str) -> JSONResponse:
    """Poll a running scan. Returns status + progress + (on success) result.

    Responses:
        200 {status: queued|scraping|computing|matching, progress: {...}}
        200 {status: done, progress: {...}, result: {...}}
        200 {status: failed, error: "...", progress: {...}}
        404: unknown / expired task_id
    """
    # Opportunistic lazy GC on reads too so long-idle processes don't leak
    if len(_TASKS) > 1000:
        _gc_old_tasks()

    task = _TASKS.get(task_id)
    if task is None:
        return JSONResponse(
            {"error": "unknown task_id — it may have expired"},
            status_code=404,
        )

    # Detect wedged tasks (running but no progress for > _POLL_STALE_SECONDS)
    if task.status in {"scraping", "computing", "matching"}:
        if time.time() - task.updated_at > _POLL_STALE_SECONDS:
            _fail(task, "scan stalled — please try again")

    return JSONResponse(task.to_dict())
