"""SoundCloud analytics — aggregation logic for the Analysis tab."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any


def aggregate_soundcloud_data(
    artist_objects: list[dict[str, Any]],
    sc_track_counts: dict[str, int] | None = None,
    sc_liked_events: list[tuple[str, str]] | list[list[str]] | None = None,
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

    # --- Time-series data (from liked_events with created_at) ---
    time_series = _build_time_series(sc_liked_events or [])

    return {
        "top_artists_by_tracks": top_by_tracks,
        "genre_distribution": genre_distribution,
        "top_artists_by_play_count": top_by_plays,
        "total_sc_artists": len(sc_artists),
        "total_liked_tracks": total_liked,
        "total_genres": len(genre_counter),
        **time_series,
    }


def _parse_date(iso_str: str) -> datetime | None:
    """Parse an ISO date string, tolerating common variations."""
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(iso_str, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except Exception:
        return None


def _build_time_series(
    liked_events: list[tuple[str, str]] | list[list[str]],
) -> dict[str, Any]:
    """Build time-series chart data from liked events.

    Returns keys:
      - cumulative_likes: [{date, total}] — running total of likes over time
      - discovery_timeline: [{month, count}] — new artists first liked per month
      - activity_heatmap: [{date, count}] — likes per day
      - has_time_data: bool — whether any timestamp data exists
    """
    # Parse events into (name, datetime) pairs
    parsed: list[tuple[str, datetime]] = []
    for event in liked_events:
        name = event[0] if isinstance(event, (list, tuple)) else ""
        ts_str = event[1] if isinstance(event, (list, tuple)) and len(event) > 1 else ""
        if not name or not ts_str:
            continue
        dt = _parse_date(ts_str)
        if dt:
            parsed.append((name, dt))

    if not parsed:
        return {
            "cumulative_likes": [],
            "discovery_timeline": [],
            "activity_heatmap": [],
            "has_time_data": False,
        }

    # Sort by date
    parsed.sort(key=lambda x: x[1])

    # --- Chart 4: Cumulative liked tracks over time ---
    # Group by day, then running total
    daily_counts: dict[str, int] = defaultdict(int)
    for _, dt in parsed:
        day_key = dt.strftime("%Y-%m-%d")
        daily_counts[day_key] += 1

    sorted_days = sorted(daily_counts.keys())
    running = 0
    cumulative_likes = []
    for day in sorted_days:
        running += daily_counts[day]
        cumulative_likes.append({"date": day, "total": running})

    # --- Chart 5: Discovery timeline — new artists first liked per month ---
    first_seen: dict[str, str] = {}  # artist → first month
    for name, dt in parsed:
        month_key = dt.strftime("%Y-%m")
        if name not in first_seen:
            first_seen[name] = month_key

    monthly_discoveries: Counter[str] = Counter(first_seen.values())
    all_months = sorted(monthly_discoveries.keys())
    discovery_timeline = [
        {"month": m, "count": monthly_discoveries[m]} for m in all_months
    ]

    # --- Chart 6: Activity heatmap — likes per day ---
    activity_heatmap = [
        {"date": day, "count": daily_counts[day]} for day in sorted_days
    ]

    return {
        "cumulative_likes": cumulative_likes,
        "discovery_timeline": discovery_timeline,
        "activity_heatmap": activity_heatmap,
        "has_time_data": True,
    }
