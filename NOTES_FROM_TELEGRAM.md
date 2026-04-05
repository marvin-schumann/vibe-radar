# Notes from Telegram (chief-of-staff handoff)

> Queue of direction/tasks sent via Marvin's Telegram chief-of-staff chat.
> Laptop session: read the **Pending** section, work items top-to-bottom, move completed items to **Done** with date + commit refs.

## Pending

_(empty)_

## Done

### 2026-04-05 — Add SoundCloud Analysis tab (3 charts)
Completed autonomously via Telegram on 2026-04-05.

- Added `/analysis` page with 3 Chart.js charts: top artists by liked tracks, genre distribution (doughnut), most-played artists by SC playback count
- New analytics module at `src/analytics/soundcloud.py`
- "Analysis" nav link added to dashboard header
- SoundCloud collector now tracks per-artist liked track counts (`track_counts`)
- `play_count` now included in `artist_objects` cache
