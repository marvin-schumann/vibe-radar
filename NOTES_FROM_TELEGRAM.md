# Notes from Telegram (chief-of-staff handoff)

> Queue of direction/tasks sent via Marvin's Telegram chief-of-staff chat.
> Laptop session: read the **Pending** section, work items top-to-bottom, move completed items to **Done** with date + commit refs.

## Pending

(none)

---

## Done

### 2026-04-09 — SoundCloud-only fallbacks for Spotify-dependent features
Implemented SoundCloud-only alternatives for the 4 Spotify-dependent features from audit:
1. **Underground Depth Score** — falls back to SoundCloud play_count percentile when no Spotify popularity
2. **Audio Features Radar** — expanded genre-to-audio-features mapping with 9 electronic sub-genres (acid techno, minimal techno, dub techno, melodic techno, progressive house, afro house, UK bass, jungle, breakbeat)
3. **Popularity display** — artist cards now show "plays 12.3K" from SoundCloud when no Spotify popularity; hidden if neither exists
4. **Exact match reason** — already source-agnostic ("in your library"), confirmed no change needed
5. **Vibe matching boost** — +15% confidence when genre Jaccard > 0.5 (compensates for missing audio features), threshold lowered 0.6 → 0.45

---

## SoundCloud-Only Audit (2026-04-09)

Full audit of which features depend on Spotify data and what breaks if Spotify is removed.

### Features that BREAK without Spotify

| Feature | File | Data used | Impact |
|---|---|---|---|
| **Underground Depth Score** | `src/api/main.py:589-638` (`/api/depth-score`) | `artist.popularity` (Spotify 0-100) | Entire endpoint returns `{score: null}`. No SoundCloud equivalent of popularity score. |
| **Audio Features Radar** | `src/visualization/taste_profile.py:113-137` | `artist.audio_features` (Spotify API) | Radar chart renders all zeros. Fallback: genre-based estimation exists in `vibe.py:68-84` but is coarse. |
| **Popularity display** | `index.html:1815` | `artist.popularity` | Shows "undefined" or missing in artist cards. |
| **Audio feature comparison in vibe matching** | `src/matching/vibe.py:224-238` | `artist.audio_features` | Loses 40% audio feature boost in confidence score; falls back to genre-only (100% Jaccard). |

### Features that DEGRADE but still work

| Feature | File | Degradation |
|---|---|---|
| **Vibe matching confidence** | `src/matching/vibe.py` | Genre-only matching (no audio feature boost). Still functional, lower confidence scores. |
| **Taste profile building** | `src/matching/vibe.py:123-176` | Always uses genre-based feature estimation (`features_estimated=True`). Works but less precise. |
| **Taste Tribe scoring** | `src/analytics/taste_dna.py:346-416` | `avg_popularity` defaults to 50 when no popularity data. Tribe assignment still works via genre keywords (50% weight) and entropy (25%). |

### Features UNAFFECTED by Spotify removal

| Feature | File | Notes |
|---|---|---|
| **Exact artist matching** | `src/matching/exact.py` | Pure name fuzzy matching. BUT: line 136 hardcodes "on your Spotify" in match reason — needs fix. |
| **Scene City** | `src/analytics/taste_dna.py:298-329` | Genre-based only. |
| **Cross-Genre Bridges** | `src/analytics/taste_dna.py:433+` | Genre family analysis only. (Fixed in this session — now electronic-only.) |
| **Dancefloor Ratio** | `src/analytics/taste_dna.py:483-525` | Genre classification only. |
| **SoundCloud Analytics** | `src/analytics/soundcloud.py` | Fully independent (time-series, track counts, bump chart, heatmap). |
| **Event collection** | `src/collectors/events/*` | Independent of music sources. |
| **DJ Twin matching** | `src/matching/dj_twin.py` | Cosine similarity on genre vectors. |
| **Shareable cards** | `src/cards/` | Uses taste DNA outputs (genre-based). |

### Proposed SoundCloud-Only Implementations

1. **Underground Depth Score** → Replace Spotify popularity with SoundCloud follower count or play count percentile. Low followers + high engagement = underground. Need to compute percentile ranking across all users' artists.
2. **Audio Features Radar** → Already has genre-based estimation fallback (`vibe.py:68-84`). Could improve the `_GENRE_AUDIO_ESTIMATES` mapping with more SC-specific genres. Mark as "estimated" in UI.
3. **Popularity display** → Replace with `play_count` from SoundCloud. Different scale but still meaningful for ranking.
4. **Exact match reason text** → Change "on your Spotify" to "in your library" (line 136 in exact.py).
5. **Vibe matching** → Genre-only matching is already the fallback and works well for electronic music where genre tags are specific.

## Done

### 2026-04-07 — DJ Twin Match (Phase 1: data pipeline + matching engine)
Completed 2026-04-07, commit 8f1811e.
- Curated 229 DJ profiles in `src/data/dj_profiles.json` (techno, house, trance, DnB, ambient, bass, disco, electro, Madrid scene)
- `src/collectors/dj_profiles.py`: batch SoundCloud scraper with rate limiting, fetches liked tracks → genre distribution vectors
- `src/data/dj_taste_vectors.json`: cached vectors for 25 test DJs (remaining 204 via background job)
- `src/matching/dj_twin.py`: cosine similarity matching engine, returns top 5 with twin/adjacent/similar classification
- `GET /api/dj-twin` endpoint in main.py
- Tested with Marvin's real data: top twin = Paul van Dyk (91%), adjacent = Above & Beyond (45%)
- Phase 2 (UI cards) pending — after card redesign is finalized

### 2026-04-07 — Redesign shareable cards with HTML/CSS + Playwright
Completed 2026-04-07.
- Replaced Pillow-based card generator with HTML/CSS templates rendered via Playwright headless Chromium
- 5 self-contained HTML templates in `src/cards/templates/` (taste_dna, scene_city, taste_tribe, cross_genre, dancefloor)
- Each template: inline CSS, Google Fonts CDN, SVG grain overlay, glassmorphism panels, radial glows, gradient borders
- Collectible card feel: gradient border (lime→orange), rarity badges (COMMON/UNCOMMON/RARE/LEGENDARY), card numbering, frequenz.live branding
- `src/cards/renderer.py`: Jinja2 templating + Playwright screenshot at 1080x1920
- API endpoints updated to use new renderer (backward-compatible same routes)
- Sample cards at `/tmp/frequenz-card-v2-*.png`

### 2026-04-07 — Shareable Instagram-story card generator
Completed 2026-04-07, commit afff99b.
- Built `src/cards/generator.py` with 5 card types: Taste DNA Summary, Scene Home City, Taste Tribe, Cross-Genre Bridge, Dancefloor vs Headphones
- Pillow + NumPy rendering at 2x (2160x3840) with LANCZOS downscale for anti-aliased output
- Downloaded Bebas Neue, Space Mono, DM Sans fonts to `src/web/static/fonts/`
- Radial glows via NumPy meshgrid, diagonal gradient overlays, rounded bars, lime/orange gradient color interpolation
- API endpoints: `GET /api/cards/{name}.png` (individual) and `GET /api/cards/all` (base64 JSON)
- Dashboard "Share Your Taste" section with 5 card previews in 9:16 aspect ratio, click-to-download
- Sample cards saved to `/tmp/frequenz-card-*.png`

### 2026-04-07 — Semi-automated invite-only onboarding flow
Completed 2026-04-07.
- Added `ADMIN_SECRET_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` to config.py
- New signup sends Telegram notification to Marvin with clickable approve link
- Admin approval endpoint `GET/POST /admin/approve?email=X&key=SECRET` sets `is_approved=true`
- Added `approve_by_email()` helper in `src/db/supabase.py`
- Updated pending.html: founding member messaging, referral placeholder copy
- Flow: signup → Telegram notification → click approve link → user gets access

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
