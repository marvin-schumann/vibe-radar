"""Genre and audio feature similarity matching."""

from __future__ import annotations

from collections import Counter

from loguru import logger

from src.config import settings
from src.models import Artist, AudioFeatures, Event, Match, MatchType, TasteProfile


# Audio feature fields used for similarity comparison
_FEATURE_FIELDS = [
    "danceability",
    "energy",
    "tempo",
    "valence",
    "acousticness",
    "instrumentalness",
    "liveness",
    "speechiness",
]

# Tempo is on a different scale (BPM ~60-200) vs the 0-1 features.
# We normalize it to 0-1 for comparison using a reasonable BPM range.
_TEMPO_MIN = 60.0
_TEMPO_MAX = 200.0


def _jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    """Compute Jaccard similarity between two sets."""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union)


def _normalize_tempo(bpm: float) -> float:
    """Normalize BPM to a 0-1 range."""
    clamped = max(_TEMPO_MIN, min(_TEMPO_MAX, bpm))
    return (clamped - _TEMPO_MIN) / (_TEMPO_MAX - _TEMPO_MIN)


def _audio_feature_similarity(
    profile_features: AudioFeatures, event_features: AudioFeatures
) -> float:
    """Compute similarity between two AudioFeatures as 1 - mean absolute difference.

    All features are on 0-1 scale (tempo is normalized).
    Returns a value between 0.0 and 1.0.
    """
    diffs: list[float] = []
    for field in _FEATURE_FIELDS:
        a = getattr(profile_features, field)
        b = getattr(event_features, field)
        if field == "tempo":
            a = _normalize_tempo(a)
            b = _normalize_tempo(b)
        diffs.append(abs(a - b))

    mean_diff = sum(diffs) / len(diffs)
    return 1.0 - mean_diff


def build_taste_profile(artists: list[Artist]) -> TasteProfile:
    """Aggregate a TasteProfile from a list of artists.

    - Counts genre occurrences across all artists.
    - Averages audio features when available.
    - Returns top_genres sorted by count descending.
    """
    genre_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()

    feature_sums: dict[str, float] = {f: 0.0 for f in _FEATURE_FIELDS}
    feature_count = 0

    for artist in artists:
        # Count genres
        for genre in artist.genres:
            genre_counter[genre.lower().strip()] += 1

        # Count sources
        source_counter[artist.source.value] += 1

        # Accumulate audio features
        if artist.audio_features is not None:
            feature_count += 1
            for field in _FEATURE_FIELDS:
                feature_sums[field] += getattr(artist.audio_features, field)

    # Build averaged audio features
    avg_features: AudioFeatures | None = None
    if feature_count > 0:
        avg_features = AudioFeatures(
            **{field: feature_sums[field] / feature_count for field in _FEATURE_FIELDS}
        )

    top_genres = genre_counter.most_common()

    logger.info(
        "Built taste profile: {} artists, {} unique genres, {} with audio features",
        len(artists),
        len(genre_counter),
        feature_count,
    )

    return TasteProfile(
        top_genres=top_genres,
        avg_features=avg_features,
        total_artists=len(artists),
        sources=dict(source_counter),
    )


class VibeMatcher:
    """Match events to user taste using genre overlap and audio feature similarity."""

    def __init__(self, threshold: float | None = None) -> None:
        self.threshold = threshold or settings.match_threshold

    def match(
        self,
        artists: list[Artist],
        events: list[Event],
        taste_profile: TasteProfile | None = None,
        exclude_event_ids: set[str] | None = None,
    ) -> list[Match]:
        """Find vibe-based matches between user taste and events.

        Args:
            artists: The user's artists (used to build a taste profile if none given).
            events: Upcoming events with genre tags.
            taste_profile: Pre-computed taste profile. Built from artists if not provided.
            exclude_event_ids: Set of event URLs to skip (e.g., already matched exactly).

        Returns:
            List of Match objects sorted by confidence descending.
        """
        if taste_profile is None:
            taste_profile = build_taste_profile(artists)

        exclude = exclude_event_ids or set()
        user_genres = {genre for genre, _count in taste_profile.top_genres}

        matches: list[Match] = []

        for event in events:
            # Skip events already matched exactly (by URL)
            if event.url and event.url in exclude:
                continue

            event_genres = {g.lower().strip() for g in event.genres}
            if not event_genres:
                continue

            # Genre similarity via Jaccard
            genre_sim = _jaccard_similarity(user_genres, event_genres)
            shared_genres = user_genres & event_genres

            # Audio feature similarity (if available)
            audio_sim: float | None = None
            if taste_profile.avg_features is not None:
                # We don't have per-event audio features in the Event model,
                # but we can look for an artist on the event whose audio features
                # we know from the user's library.
                audio_sim = self._event_audio_similarity(
                    event, artists, taste_profile.avg_features
                )

            # Combine scores: genre similarity is primary, audio is a boost
            if audio_sim is not None:
                confidence = 0.6 * genre_sim + 0.4 * audio_sim
            else:
                confidence = genre_sim

            if confidence < self.threshold:
                continue

            confidence = round(min(confidence, 1.0), 4)
            reason = self._build_reason(shared_genres, genre_sim, audio_sim)

            # Pick the "best" matching artist for the Match object.
            # Use the artist with the most genre overlap with the event.
            best_artist = self._pick_best_artist(artists, event_genres)

            logger.debug(
                "Vibe match: {} (confidence={:.2f}, genres={})",
                event.name,
                confidence,
                shared_genres,
            )

            matches.append(
                Match(
                    event=event,
                    matched_artist=best_artist,
                    event_artist_name=event.artists[0] if event.artists else event.name,
                    match_type=MatchType.VIBE,
                    confidence=confidence,
                    match_reason=reason,
                )
            )

        matches.sort(key=lambda m: (-m.confidence, m.event.date))
        return matches

    def _event_audio_similarity(
        self,
        event: Event,
        artists: list[Artist],
        avg_features: AudioFeatures,
    ) -> float | None:
        """Check if any event artist exists in the user's library with audio features.

        If so, compute audio feature similarity against the user's average profile.
        """
        event_normalized = {a.lower().strip() for a in event.artists}
        best_sim: float | None = None

        for artist in artists:
            if artist.audio_features is None:
                continue
            if artist.normalized_name in event_normalized:
                sim = _audio_feature_similarity(avg_features, artist.audio_features)
                if best_sim is None or sim > best_sim:
                    best_sim = sim

        return best_sim

    @staticmethod
    def _pick_best_artist(artists: list[Artist], event_genres: set[str]) -> Artist:
        """Pick the user artist with the most genre overlap to the event."""
        best: Artist | None = None
        best_overlap = -1

        for artist in artists:
            artist_genres = {g.lower().strip() for g in artist.genres}
            overlap = len(artist_genres & event_genres)
            if overlap > best_overlap:
                best_overlap = overlap
                best = artist

        # Fallback: just use the first artist
        return best if best is not None else artists[0]

    @staticmethod
    def _build_reason(
        shared_genres: set[str],
        genre_sim: float,
        audio_sim: float | None,
    ) -> str:
        """Build a human-readable match reason."""
        parts: list[str] = []

        if shared_genres:
            genre_list = ", ".join(sorted(shared_genres)[:5])
            parts.append(
                f"Genre match: {genre_list} ({len(shared_genres)} shared genre"
                f"{'s' if len(shared_genres) != 1 else ''})"
            )

        if audio_sim is not None and audio_sim > 0.5:
            # Describe the dominant audio characteristic
            parts.append(
                "Vibe match: high energy + danceability similar to your taste"
            )

        if not parts:
            parts.append(f"Style similarity: {genre_sim:.0%} genre overlap")

        return " | ".join(parts)
