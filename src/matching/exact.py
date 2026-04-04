"""Exact artist matching using fuzzy string comparison."""

from __future__ import annotations

import re

from loguru import logger
from thefuzz import fuzz

from src.config import settings
from src.models import Artist, Event, Match, MatchType


# Common prefixes/suffixes to strip for better matching
_STRIP_TOKENS = {"dj", "mc", "the", "djs", "live", "b2b"}


def _normalize_artist_name(name: str) -> str:
    """Normalize an artist name for comparison.

    Lowercase, strip whitespace, remove common prefixes like 'DJ'/'MC',
    and remove special characters.
    """
    name = name.lower().strip()

    # Remove special characters (keep alphanumeric and spaces)
    name = re.sub(r"[^a-z0-9\s]", "", name)

    # Remove common prefixes/suffixes
    tokens = name.split()
    tokens = [t for t in tokens if t not in _STRIP_TOKENS]

    return " ".join(tokens).strip()


class ExactMatcher:
    """Match user artists to event line-ups using fuzzy string comparison."""

    def __init__(self, threshold: int | None = None) -> None:
        self.threshold = threshold or settings.fuzzy_match_threshold

    def match(self, artists: list[Artist], events: list[Event]) -> list[Match]:
        """Find exact/fuzzy artist name matches between the user's library and events.

        Args:
            artists: The user's artists from Spotify/SoundCloud.
            events: Upcoming events with artist line-ups.

        Returns:
            List of Match objects sorted by confidence descending.
        """
        matches: list[Match] = []

        # Pre-compute normalized names for user artists
        artist_lookup: list[tuple[Artist, str, str]] = []
        for artist in artists:
            raw = artist.name.lower().strip()
            normalized = _normalize_artist_name(artist.name)
            artist_lookup.append((artist, raw, normalized))

        for event in events:
            for event_artist_name in event.artists:
                event_raw = event_artist_name.lower().strip()
                event_normalized = _normalize_artist_name(event_artist_name)

                best_match: tuple[Artist, int, str] | None = None

                for artist, artist_raw, artist_normalized in artist_lookup:
                    # Try raw name comparison first
                    ratio = fuzz.ratio(artist_raw, event_raw)
                    partial = fuzz.partial_ratio(artist_raw, event_raw)
                    score_raw = max(ratio, partial)

                    # Also try normalized (without DJ/MC prefixes etc.)
                    ratio_norm = fuzz.ratio(artist_normalized, event_normalized)
                    partial_norm = fuzz.partial_ratio(
                        artist_normalized, event_normalized
                    )
                    score_norm = max(ratio_norm, partial_norm)

                    score = max(score_raw, score_norm)

                    if score >= self.threshold:
                        if best_match is None or score > best_match[1]:
                            best_match = (artist, score, event_artist_name)

                if best_match is not None:
                    artist_obj, score, matched_event_name = best_match
                    confidence = self._score_to_confidence(score)
                    reason = self._build_reason(
                        artist_obj.name, matched_event_name, score
                    )

                    logger.debug(
                        "Exact match: {} ~ {} (score={}, confidence={:.2f})",
                        artist_obj.name,
                        matched_event_name,
                        score,
                        confidence,
                    )

                    matches.append(
                        Match(
                            event=event,
                            matched_artist=artist_obj,
                            event_artist_name=matched_event_name,
                            match_type=MatchType.EXACT,
                            confidence=confidence,
                            match_reason=reason,
                        )
                    )

        # Sort by confidence descending
        matches.sort(key=lambda m: (-m.confidence, m.event.date))
        return matches

    def _score_to_confidence(self, score: int) -> float:
        """Convert a fuzz score (threshold..100) to confidence (0..1).

        A score of 100 maps to 1.0. The threshold maps to 0.0.
        Scores scale linearly in between.
        """
        if score >= 100:
            return 1.0
        # Scale from threshold..100 -> 0..1
        span = 100 - self.threshold
        if span <= 0:
            return 1.0
        return round((score - self.threshold) / span, 4)

    @staticmethod
    def _build_reason(artist_name: str, event_artist_name: str, score: int) -> str:
        """Build a human-readable match reason."""
        if score == 100:
            return f"Exact match: '{event_artist_name}' on your Spotify"
        return (
            f"Close match: '{event_artist_name}' \u2248 '{artist_name}' "
            f"({score}% similar)"
        )
