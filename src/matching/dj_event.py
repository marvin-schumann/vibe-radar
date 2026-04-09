"""DJ-profile-based event matching.

Matches event performers against the user's taste by looking up each
performer in the cached DJ taste vectors and computing cosine similarity
between the DJ's genre distribution and the user's genre distribution.

This supplements exact matching and genre-based vibe matching — it can
surface events where the user hasn't liked the performing artist directly,
but that artist's taste profile is close to the user's.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from thefuzz import fuzz

from src.matching.dj_twin import _cosine_similarity, load_dj_vectors
from src.models import Artist, Event, Match, MatchType


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_FUZZY_THRESHOLD = 85       # Minimum fuzz ratio to consider a name match
_SIMILARITY_THRESHOLD = 0.3  # Minimum cosine similarity to create a match

# Module-level cache for DJ vectors (loaded once)
_dj_vectors: dict[str, dict[str, Any]] | None = None
_dj_name_lookup: dict[str, str] | None = None  # normalized_name → original_name


def _ensure_vectors_loaded() -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Load DJ vectors once and cache them at module level."""
    global _dj_vectors, _dj_name_lookup
    if _dj_vectors is None:
        _dj_vectors = load_dj_vectors()
        _dj_name_lookup = {name.lower().strip(): name for name in _dj_vectors}
    return _dj_vectors, _dj_name_lookup


def _find_dj_match(
    artist_name: str,
    dj_vectors: dict[str, dict[str, Any]],
    name_lookup: dict[str, str],
) -> tuple[str, dict[str, Any]] | None:
    """Fuzzy-match an event artist name against DJ profile names.

    Returns (dj_name, dj_data) if a match is found, else None.
    """
    normalized = artist_name.lower().strip()

    # Fast path: exact normalized match
    if normalized in name_lookup:
        original = name_lookup[normalized]
        return original, dj_vectors[original]

    # Fuzzy matching against all DJ names (ratio only — no partial_ratio
    # to avoid false positives with short/substring DJ names)
    best_score = 0
    best_match: tuple[str, dict[str, Any]] | None = None

    for norm_name, original_name in name_lookup.items():
        score = fuzz.ratio(normalized, norm_name)
        if score > best_score:
            best_score = score
            best_match = (original_name, dj_vectors[original_name])

    if best_score >= _FUZZY_THRESHOLD and best_match is not None:
        return best_match

    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def match_events_via_dj_profiles(
    user_genres: dict[str, float],
    events: list[Event],
    *,
    exclude_event_urls: set[str] | None = None,
    similarity_threshold: float = _SIMILARITY_THRESHOLD,
    placeholder_artist: Artist | None = None,
) -> list[Match]:
    """Match events by comparing DJ taste vectors to the user's genre profile.

    Args:
        user_genres: User's genre distribution {genre: count_or_pct}.
        events: List of events to match against.
        exclude_event_urls: Event URLs already matched (skip these).
        similarity_threshold: Minimum cosine similarity to include.
        placeholder_artist: Artist object to use as matched_artist in results
            (since the match is taste-based, not artist-based).

    Returns:
        List of Match objects sorted by confidence descending.
    """
    if not user_genres or not events:
        return []

    dj_vectors, name_lookup = _ensure_vectors_loaded()
    if not dj_vectors:
        logger.warning("No DJ taste vectors available for event matching")
        return []

    exclude = exclude_event_urls or set()
    # Normalize user genres
    user_vec = {g.lower().strip(): float(v) for g, v in user_genres.items()}

    # Build a placeholder artist for the match (taste-based, no specific artist)
    if placeholder_artist is None:
        from src.models import MusicSource
        placeholder_artist = Artist(
            name="Your Taste Profile",
            source=MusicSource.SOUNDCLOUD,
        )

    matches: list[Match] = []
    seen_event_urls: set[str] = set()

    for event in events:
        # Skip already-matched events
        if event.url and event.url in exclude:
            continue
        # Skip duplicate events within this matcher
        if event.url and event.url in seen_event_urls:
            continue

        best_sim = 0.0
        best_dj_name = ""

        for artist_name in event.artists:
            result = _find_dj_match(artist_name, dj_vectors, name_lookup)
            if result is None:
                continue

            dj_name, dj_data = result
            dj_genre_dist = dj_data.get("genre_distribution", {})
            if not dj_genre_dist:
                continue

            sim = _cosine_similarity(user_vec, dj_genre_dist)
            if sim > best_sim:
                best_sim = sim
                best_dj_name = dj_name

        if best_sim >= similarity_threshold:
            sim_pct = round(best_sim * 100)
            matches.append(Match(
                event=event,
                matched_artist=placeholder_artist,
                event_artist_name=best_dj_name,
                match_type=MatchType.VIBE,
                confidence=round(best_sim, 4),
                match_reason=f"DJ similarity: {best_dj_name} has {sim_pct}% taste overlap with you",
            ))
            if event.url:
                seen_event_urls.add(event.url)

    matches.sort(key=lambda m: (-m.confidence, m.event.date))
    logger.info(
        "DJ event matching: {} matches from {} events (threshold={:.0%})",
        len(matches), len(events), similarity_threshold,
    )
    return matches
