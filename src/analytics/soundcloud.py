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

    # --- Build artist → primary genre mapping for time-series charts ---
    artist_genre_map: dict[str, str] = {}
    for a in sc_artists:
        key = a["name"].lower().strip()
        genres = a.get("genres") or []
        if genres:
            artist_genre_map[key] = genres[0].lower()

    # --- Time-series data (from liked_events with created_at) ---
    time_series = _build_time_series(sc_liked_events or [], artist_genre_map)

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
    artist_genre_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build time-series chart data from liked events.

    Returns keys:
      - bump_chart: {months, artists: [{name, ranks}]} — top 10 artist rank over time
      - calendar_heatmap: [{date, count}] — daily likes for GitHub-style heatmap
      - genre_area: {months, genres: [{genre, data}]} — stacked area by genre
      - has_time_data: bool
    """
    genre_map = artist_genre_map or {}

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
            "bump_chart": {},
            "calendar_heatmap": [],
            "genre_area": {},
            "has_time_data": False,
        }

    parsed.sort(key=lambda x: x[1])

    # --- Daily counts (used by calendar heatmap) ---
    daily_counts: dict[str, int] = defaultdict(int)
    for _, dt in parsed:
        daily_counts[dt.strftime("%Y-%m-%d")] += 1

    # --- Bump chart: top 10 artists' rank over time (monthly) ---
    # Collect all months
    all_months_set: set[str] = set()
    for _, dt in parsed:
        all_months_set.add(dt.strftime("%Y-%m"))
    all_months = sorted(all_months_set)

    # Build cumulative likes per artist per month
    cumul_by_artist: dict[str, int] = defaultdict(int)
    month_snapshots: dict[str, dict[str, int]] = {}
    event_idx = 0
    for month in all_months:
        while event_idx < len(parsed) and parsed[event_idx][1].strftime("%Y-%m") <= month:
            cumul_by_artist[parsed[event_idx][0]] += 1
            event_idx += 1
        month_snapshots[month] = dict(cumul_by_artist)

    # Determine top 10 by final month
    final_snapshot = month_snapshots[all_months[-1]] if all_months else {}
    top10_names = [
        name
        for name, _ in sorted(final_snapshot.items(), key=lambda x: x[1], reverse=True)[:10]
    ]

    bump_artists = []
    for name in top10_names:
        ranks = []
        for month in all_months:
            snap = month_snapshots[month]
            # Rank among top10 only (based on their cumulative count that month)
            counts_this_month = [(n, snap.get(n, 0)) for n in top10_names]
            counts_this_month.sort(key=lambda x: x[1], reverse=True)
            rank = next(
                (i + 1 for i, (n, _) in enumerate(counts_this_month) if n == name),
                len(top10_names),
            )
            ranks.append(rank)
        bump_artists.append({"name": name, "ranks": ranks})

    bump_chart = {"months": all_months, "artists": bump_artists}

    # --- Calendar heatmap: [{date, count}] ---
    sorted_days = sorted(daily_counts.keys())
    calendar_heatmap = [
        {"date": day, "count": daily_counts[day]} for day in sorted_days
    ]

    # --- Stacked area by genre over time ---
    # Group events by month and genre
    monthly_genre_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for name, dt in parsed:
        genre = genre_map.get(name, "other")
        month_key = dt.strftime("%Y-%m")
        monthly_genre_counts[month_key][genre] += 1

    # Collect all genres, keep top 6, rest as "other"
    total_genre_counts: Counter[str] = Counter()
    for mc in monthly_genre_counts.values():
        total_genre_counts.update(mc)
    top_genres = [g for g, _ in total_genre_counts.most_common(6) if g != "other"]
    if len(top_genres) < 6 and "other" not in top_genres:
        top_genres.append("other")

    # Build cumulative data per genre per month
    genre_cumul: dict[str, int] = defaultdict(int)
    genre_area_series: dict[str, list[int]] = {g: [] for g in top_genres}

    for month in all_months:
        mc = monthly_genre_counts.get(month, Counter())
        # Accumulate for top genres; lump the rest into "other"
        other_count = 0
        for genre, count in mc.items():
            if genre in top_genres and genre != "other":
                genre_cumul[genre] += count
            else:
                other_count += count
        genre_cumul["other"] = genre_cumul.get("other", 0) + other_count

        for g in top_genres:
            genre_area_series[g].append(genre_cumul.get(g, 0))

    genre_area = {
        "months": all_months,
        "genres": [{"genre": g, "data": genre_area_series[g]} for g in top_genres],
    }

    return {
        "bump_chart": bump_chart,
        "calendar_heatmap": calendar_heatmap,
        "genre_area": genre_area,
        "has_time_data": True,
    }
