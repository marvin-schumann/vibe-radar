# Notes from Telegram (chief-of-staff handoff)

> Queue of direction/tasks sent via Marvin's Telegram chief-of-staff chat.
> Laptop session: read the **Pending** section, work items top-to-bottom, move completed items to **Done** with date + commit refs.

## Pending

(none)

## Done

### 2026-04-06 — Make Spotify optional, SoundCloud-first
Completed 2026-04-06.
- Pivoted onboarding to SoundCloud-first: SoundCloud card is now primary with "Required" badge, Spotify is secondary with "Optional" badge and muted styling
- "Go to Dashboard" button appears once SoundCloud is connected (no longer requires Spotify)
- Dashboard hides Spotify platform filter button when user has no Spotify data
- Pipeline already handled both sources conditionally — no backend changes needed
- Auth toasts genericized (not Spotify-specific)

### 2026-04-05 — Replace boring Over Time charts with cooler ones
Completed 2026-04-05.
- Replaced 3 boring "Over Time" charts with visually cooler alternatives:
  1. **Bump chart** — top 10 artists' rank over time (monthly, inverted y-axis, top 3 get thicker lines)
  2. **GitHub-style calendar heatmap** — daily listening activity using `chartjs-chart-matrix` plugin
  3. **Stacked area by genre** — cumulative likes broken down by top 6 genres over time
- Built artist→genre join in backend using SC artist `genres[0]` field
- Kept original 3 charts (top artists, genre doughnut, top by plays) untouched
- Empty-data fallback preserved across all 3 new charts

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
