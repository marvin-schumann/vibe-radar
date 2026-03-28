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

# Genre → estimated audio features (energy, danceability, valence, acousticness,
# instrumentalness, liveness, speechiness, tempo).
# Used as fallback when Spotify's audio features API is unavailable.
_GENRE_AUDIO_ESTIMATES: dict[str, dict[str, float]] = {
    "techno":           dict(energy=0.90, danceability=0.80, valence=0.30, acousticness=0.02, instrumentalness=0.87, liveness=0.15, speechiness=0.05, tempo=135),
    "hard techno":      dict(energy=0.95, danceability=0.83, valence=0.25, acousticness=0.01, instrumentalness=0.90, liveness=0.15, speechiness=0.04, tempo=145),
    "trance":           dict(energy=0.86, danceability=0.76, valence=0.65, acousticness=0.03, instrumentalness=0.76, liveness=0.15, speechiness=0.04, tempo=140),
    "psytrance":        dict(energy=0.91, danceability=0.80, valence=0.55, acousticness=0.02, instrumentalness=0.82, liveness=0.15, speechiness=0.04, tempo=145),
    "drum and bass":    dict(energy=0.91, danceability=0.84, valence=0.45, acousticness=0.02, instrumentalness=0.80, liveness=0.16, speechiness=0.05, tempo=172),
    "dnb":              dict(energy=0.91, danceability=0.84, valence=0.45, acousticness=0.02, instrumentalness=0.80, liveness=0.16, speechiness=0.05, tempo=172),
    "dubstep":          dict(energy=0.88, danceability=0.79, valence=0.40, acousticness=0.02, instrumentalness=0.76, liveness=0.15, speechiness=0.05, tempo=140),
    "hardstyle":        dict(energy=0.95, danceability=0.85, valence=0.35, acousticness=0.01, instrumentalness=0.85, liveness=0.15, speechiness=0.04, tempo=150),
    "house":            dict(energy=0.80, danceability=0.86, valence=0.62, acousticness=0.04, instrumentalness=0.70, liveness=0.16, speechiness=0.05, tempo=124),
    "deep house":       dict(energy=0.70, danceability=0.82, valence=0.58, acousticness=0.05, instrumentalness=0.67, liveness=0.15, speechiness=0.05, tempo=122),
    "edm":              dict(energy=0.85, danceability=0.80, valence=0.65, acousticness=0.03, instrumentalness=0.70, liveness=0.16, speechiness=0.05, tempo=130),
    "electronic":       dict(energy=0.76, danceability=0.75, valence=0.52, acousticness=0.05, instrumentalness=0.70, liveness=0.14, speechiness=0.05, tempo=125),
    "dance & edm":      dict(energy=0.84, danceability=0.82, valence=0.64, acousticness=0.03, instrumentalness=0.65, liveness=0.16, speechiness=0.05, tempo=128),
    "acid":             dict(energy=0.86, danceability=0.80, valence=0.36, acousticness=0.02, instrumentalness=0.85, liveness=0.15, speechiness=0.04, tempo=135),
    "minimal":          dict(energy=0.72, danceability=0.78, valence=0.35, acousticness=0.03, instrumentalness=0.88, liveness=0.13, speechiness=0.04, tempo=128),
    "ambient":          dict(energy=0.20, danceability=0.35, valence=0.45, acousticness=0.55, instrumentalness=0.88, liveness=0.10, speechiness=0.03, tempo=80),
    "hip hop":          dict(energy=0.65, danceability=0.80, valence=0.55, acousticness=0.10, instrumentalness=0.12, liveness=0.16, speechiness=0.22, tempo=90),
    "rap":              dict(energy=0.65, danceability=0.80, valence=0.50, acousticness=0.08, instrumentalness=0.08, liveness=0.16, speechiness=0.28, tempo=88),
    "trap":             dict(energy=0.75, danceability=0.80, valence=0.45, acousticness=0.05, instrumentalness=0.22, liveness=0.14, speechiness=0.20, tempo=140),
    "phonk":            dict(energy=0.78, danceability=0.79, valence=0.42, acousticness=0.05, instrumentalness=0.35, liveness=0.14, speechiness=0.14, tempo=138),
    "pop":              dict(energy=0.72, danceability=0.75, valence=0.75, acousticness=0.12, instrumentalness=0.07, liveness=0.14, speechiness=0.07, tempo=120),
    "indie pop":        dict(energy=0.65, danceability=0.68, valence=0.65, acousticness=0.22, instrumentalness=0.12, liveness=0.14, speechiness=0.05, tempo=115),
    "rock":             dict(energy=0.76, danceability=0.64, valence=0.55, acousticness=0.10, instrumentalness=0.14, liveness=0.17, speechiness=0.05, tempo=130),
    "metal":            dict(energy=0.91, danceability=0.58, valence=0.34, acousticness=0.04, instrumentalness=0.28, liveness=0.17, speechiness=0.05, tempo=145),
    "anime":            dict(energy=0.76, danceability=0.70, valence=0.65, acousticness=0.05, instrumentalness=0.18, liveness=0.14, speechiness=0.07, tempo=130),
    "soundtrack":       dict(energy=0.50, danceability=0.50, valence=0.50, acousticness=0.32, instrumentalness=0.62, liveness=0.12, speechiness=0.04, tempo=100),
    "score":            dict(energy=0.40, danceability=0.40, valence=0.45, acousticness=0.40, instrumentalness=0.76, liveness=0.12, speechiness=0.04, tempo=95),
    "jazz":             dict(energy=0.52, danceability=0.65, valence=0.65, acousticness=0.38, instrumentalness=0.42, liveness=0.18, speechiness=0.05, tempo=105),
    "classical":        dict(energy=0.25, danceability=0.38, valence=0.50, acousticness=0.86, instrumentalness=0.90, liveness=0.12, speechiness=0.03, tempo=100),
    "soul":             dict(energy=0.65, danceability=0.70, valence=0.70, acousticness=0.22, instrumentalness=0.12, liveness=0.17, speechiness=0.07, tempo=98),
    "funk":             dict(energy=0.76, danceability=0.86, valence=0.76, acousticness=0.10, instrumentalness=0.22, liveness=0.17, speechiness=0.07, tempo=110),
}


def _estimate_features_from_genres(genre_counts: Counter[str]) -> AudioFeatures | None:
    """Estimate AudioFeatures from top genres when the Spotify API is unavailable."""
    total_weight = 0.0
    sums: dict[str, float] = {f: 0.0 for f in _FEATURE_FIELDS}

    for genre, count in genre_counts.most_common(20):
        genre_key = genre.lower().strip()
        if genre_key in _GENRE_AUDIO_ESTIMATES:
            w = float(count)
            total_weight += w
            for field, val in _GENRE_AUDIO_ESTIMATES[genre_key].items():
                sums[field] += val * w

    if total_weight == 0.0:
        return None

    return AudioFeatures(**{f: sums[f] / total_weight for f in _FEATURE_FIELDS})


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

    # Build averaged audio features; fall back to genre-based estimation
    avg_features: AudioFeatures | None = None
    features_estimated = False
    if feature_count > 0:
        avg_features = AudioFeatures(
            **{field: feature_sums[field] / feature_count for field in _FEATURE_FIELDS}
        )
    else:
        avg_features = _estimate_features_from_genres(genre_counter)
        features_estimated = avg_features is not None

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
        features_estimated=features_estimated,
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
