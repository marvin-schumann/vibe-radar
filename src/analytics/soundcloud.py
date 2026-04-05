"""SoundCloud analytics — aggregation logic for the Analysis tab."""

from __future__ import annotations

from collections import Counter
from typing import Any


def aggregate_soundcloud_data(
    artist_objects: list[dict[str, Any]],
    sc_track_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Build all SoundCloud analysis data from cached artist objects.

    Returns a dict with three chart payloads:
      - top_artists_by_tracks: artists ranked by number of liked tracks
      - genre_distribution: genre counts across SC artists
      - top_artists_by_play_count: artists ranked by track playback count
    """
    sc_artists = [a for a in artist_objects if a.get("source") == "soundcloud"]

    if not sc_artists:
        return {
            "top_artists_by_tracks": [],
            "genre_distribution": [],
            "top_artists_by_play_count": [],
            "total_sc_artists": 0,
            "total_liked_tracks": 0,
            "total_genres": 0,
        }

    track_counts = sc_track_counts or {}

    # --- Chart 1: Top artists by liked track count ---
    artists_with_counts = []
    for a in sc_artists:
        key = a["name"].lower().strip()
        count = track_counts.get(key, 1)
        artists_with_counts.append({"name": a["name"], "count": count})

    artists_with_counts.sort(key=lambda x: x["count"], reverse=True)
    top_by_tracks = artists_with_counts[:15]

    # --- Chart 2: Genre distribution ---
    genre_counter: Counter[str] = Counter()
    for a in sc_artists:
        for genre in a.get("genres", []):
            if genre:
                genre_counter[genre.lower()] += 1

    genre_distribution = [
        {"genre": genre, "count": count}
        for genre, count in genre_counter.most_common(12)
    ]

    # --- Chart 3: Top artists by play count (track popularity) ---
    artists_with_plays = [
        {"name": a["name"], "play_count": a.get("play_count") or 0}
        for a in sc_artists
        if a.get("play_count")
    ]
    artists_with_plays.sort(key=lambda x: x["play_count"], reverse=True)
    top_by_plays = artists_with_plays[:15]

    total_liked = sum(track_counts.values()) if track_counts else len(sc_artists)

    return {
        "top_artists_by_tracks": top_by_tracks,
        "genre_distribution": genre_distribution,
        "top_artists_by_play_count": top_by_plays,
        "total_sc_artists": len(sc_artists),
        "total_liked_tracks": total_liked,
        "total_genres": len(genre_counter),
    }
