# Notes from Telegram (chief-of-staff handoff)

> Queue of direction/tasks sent via Marvin's Telegram chief-of-staff chat.
> Laptop session: read the **Pending** section, work items top-to-bottom, move completed items to **Done** with date + commit refs.

## Pending

(empty)

## Done

### 2026-04-05 — Add time-based SoundCloud charts to /analysis
Completed 2026-04-05.
- Fixed SoundCloud collector to capture `created_at` timestamp from liked-track items (was being discarded)
- Added `liked_events` list of `(normalized_name, created_at)` tuples to collector, persisted in cache
- Added 3 "Over Time" charts below existing 3: cumulative likes (line), artist discovery timeline (bar), activity heatmap (bar with intensity)
- Graceful empty-data handling: shows "refresh your SoundCloud data" message when no timestamp data available
- All analytics in `src/analytics/soundcloud.py`, no new modules

### 2026-04-05 — UI coherence cleanup (header/nav across tabs + matches counter)
Completed 2026-04-05.
- Unified header layout: both pages now 2-column (logo left, nav right), logo always links to /
- Moved status bar out of header into content area, eliminating the "half button" layout
- Matched `.gp-btn` styling (borders, hover, active states) between both pages
- Fixed matches counter: status bar now uses `countUniqueEvents()` (unique events, not per-artist match entries)
- Added "Dashboard" active indicator to dashboard nav for symmetry with analysis page

### 2026-04-05 — Add SoundCloud Analysis tab (3 charts)
Completed autonomously via Telegram on 2026-04-05.

- Added `/analysis` page with 3 Chart.js charts: top artists by liked tracks, genre distribution (doughnut), most-played artists by SC playback count
- New analytics module at `src/analytics/soundcloud.py`
- "Analysis" nav link added to dashboard header
- SoundCloud collector now tracks per-artist liked track counts (`track_counts`)
- `play_count` now included in `artist_objects` cache
