"""Generate visualization data for the user's music taste profile.

Produces Chart.js-compatible data structures for the web dashboard
and rich text summaries for CLI output.
"""

from __future__ import annotations

from src.matching.vibe import build_taste_profile
from src.models import Artist, AudioFeatures, MusicSource, TasteProfile

# Neon color palette
_CYAN = "#00f0ff"
_MAGENTA = "#ff00aa"

# Audio feature labels displayed on charts (order matches AudioFeatures fields)
_RADAR_LABELS = [
    "Danceability",
    "Energy",
    "Valence",
    "Acousticness",
    "Instrumentalness",
    "Liveness",
    "Speechiness",
]

_RADAR_FIELDS = [
    "danceability",
    "energy",
    "valence",
    "acousticness",
    "instrumentalness",
    "liveness",
    "speechiness",
]

# Source display names and brand colors
_SOURCE_COLORS: dict[str, str] = {
    MusicSource.SPOTIFY.value: "#1DB954",
    MusicSource.SOUNDCLOUD.value: "#FF5500",
}

_SOURCE_LABELS: dict[str, str] = {
    MusicSource.SPOTIFY.value: "Spotify",
    MusicSource.SOUNDCLOUD.value: "SoundCloud",
}

# Block characters for CLI bar rendering
_FULL_BLOCK = "\u2588"
_HALF_BLOCK = "\u2584"


def _gradient_colors(n: int) -> list[str]:
    """Generate *n* hex colors forming a linear gradient from cyan to magenta.

    Cyan  (#00f0ff) -> Magenta (#ff00aa).
    Interpolates each RGB channel independently.
    """
    if n <= 0:
        return []
    if n == 1:
        return [_CYAN]

    r_start, g_start, b_start = 0x00, 0xF0, 0xFF
    r_end, g_end, b_end = 0xFF, 0x00, 0xAA

    colors: list[str] = []
    for i in range(n):
        t = i / (n - 1)
        r = int(r_start + (r_end - r_start) * t)
        g = int(g_start + (g_end - g_start) * t)
        b = int(b_start + (b_end - b_start) * t)
        colors.append(f"#{r:02x}{g:02x}{b:02x}")
    return colors


class TasteVisualizer:
    """Build visualization payloads from a list of collected artists."""

    def __init__(self, artists: list[Artist]) -> None:
        self._artists = artists
        self._profile: TasteProfile = build_taste_profile(artists)

    # ------------------------------------------------------------------
    # Chart.js data generators
    # ------------------------------------------------------------------

    def get_genre_chart_data(self, top_n: int = 15) -> dict:
        """Return Chart.js-compatible data for a horizontal bar chart of top genres.

        Args:
            top_n: Maximum number of genres to include.

        Returns:
            A dict with ``labels`` and ``datasets`` keys ready for Chart.js.
        """
        genres = self._profile.top_genres[:top_n]
        labels = [genre for genre, _count in genres]
        data = [count for _genre, count in genres]
        colors = _gradient_colors(len(labels))

        return {
            "labels": labels,
            "datasets": [
                {
                    "label": "Genre frequency",
                    "data": data,
                    "backgroundColor": colors,
                },
            ],
        }

    def get_audio_features_radar_data(self) -> dict:
        """Return Chart.js-compatible data for a radar chart of average audio features.

        If no audio features are available the dataset values will all be zero.

        Returns:
            A dict with ``labels`` and ``datasets`` keys ready for Chart.js.
        """
        features = self._profile.avg_features
        if features is not None:
            data = [round(getattr(features, field), 4) for field in _RADAR_FIELDS]
        else:
            data = [0.0] * len(_RADAR_FIELDS)

        return {
            "labels": list(_RADAR_LABELS),
            "datasets": [
                {
                    "label": "Your Taste",
                    "data": data,
                    "borderColor": _CYAN,
                    "backgroundColor": "rgba(0, 240, 255, 0.2)",
                },
            ],
        }

    def get_source_breakdown(self) -> dict:
        """Return Chart.js pie chart data showing artist count by source.

        Returns:
            A dict with ``labels`` and ``datasets`` keys ready for Chart.js.
        """
        labels: list[str] = []
        data: list[int] = []
        colors: list[str] = []

        for source_value, count in self._profile.sources.items():
            labels.append(_SOURCE_LABELS.get(source_value, source_value))
            data.append(count)
            colors.append(_SOURCE_COLORS.get(source_value, "#888888"))

        return {
            "labels": labels,
            "datasets": [
                {
                    "data": data,
                    "backgroundColor": colors,
                },
            ],
        }

    # ------------------------------------------------------------------
    # CLI text output
    # ------------------------------------------------------------------

    def get_taste_summary_text(self) -> str:
        """Return a rich-formatted text summary suitable for CLI output.

        Includes:
        - Top 10 genres with block-character bar chart
        - Audio feature profile as labeled bars
        - Source breakdown counts
        """
        sections: list[str] = []

        # --- Top Genres ---
        sections.append(self._render_genre_bars(top_n=10))

        # --- Audio Features ---
        sections.append(self._render_feature_bars())

        # --- Source Breakdown ---
        sections.append(self._render_source_breakdown())

        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Combined payload
    # ------------------------------------------------------------------

    def get_full_profile_data(self) -> dict:
        """Return all chart data combined in a single dict for the API endpoint.

        Keys: ``genre_chart``, ``audio_features_radar``, ``source_breakdown``,
        ``total_artists``.
        """
        return {
            "genre_chart": self.get_genre_chart_data(),
            "audio_features_radar": self.get_audio_features_radar_data(),
            "source_breakdown": self.get_source_breakdown(),
            "total_artists": self._profile.total_artists,
        }

    # ------------------------------------------------------------------
    # Private helpers for CLI rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _bar(value: float, max_value: float, width: int = 30) -> str:
        """Render a horizontal bar using block characters.

        Args:
            value: The current value.
            max_value: The maximum possible value (used to scale the bar).
            width: Maximum bar width in characters.
        """
        if max_value <= 0:
            return ""
        filled = int(round(value / max_value * width))
        return _FULL_BLOCK * filled

    def _render_genre_bars(self, top_n: int = 10) -> str:
        genres = self._profile.top_genres[:top_n]
        if not genres:
            return "-- Top Genres --\n  (no genre data)"

        max_count = genres[0][1] if genres else 1
        # Determine label padding width
        pad = max(len(genre) for genre, _ in genres)

        lines: list[str] = ["-- Top Genres --"]
        for genre, count in genres:
            bar = self._bar(count, max_count)
            lines.append(f"  {genre:<{pad}}  {bar} {count}")
        return "\n".join(lines)

    def _render_feature_bars(self) -> str:
        features = self._profile.avg_features
        lines: list[str] = ["-- Audio Profile --"]

        if features is None:
            lines.append("  (no audio feature data)")
            return "\n".join(lines)

        pad = max(len(label) for label in _RADAR_LABELS)
        for label, field in zip(_RADAR_LABELS, _RADAR_FIELDS):
            value = getattr(features, field)
            bar = self._bar(value, 1.0, width=25)
            lines.append(f"  {label:<{pad}}  {bar} {value:.2f}")
        return "\n".join(lines)

    def _render_source_breakdown(self) -> str:
        lines: list[str] = ["-- Sources --"]
        total = self._profile.total_artists
        if not self._profile.sources:
            lines.append("  (no source data)")
            return "\n".join(lines)

        for source_value, count in self._profile.sources.items():
            label = _SOURCE_LABELS.get(source_value, source_value)
            pct = (count / total * 100) if total else 0
            lines.append(f"  {label}: {count} artists ({pct:.0f}%)")
        lines.append(f"  Total: {total} artists")
        return "\n".join(lines)
