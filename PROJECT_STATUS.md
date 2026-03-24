# Vibe Radar — Project Status

> Last updated: 2026-03-24

## What This Is

A web app that matches your Spotify + SoundCloud music taste to upcoming DJ/music events in your city. Built for Madrid, designed to scale to other cities and become a commercial product.

## Repos

- **Main app** (private): https://github.com/marvin-schumann/vibe-radar
  - Local path: `~/madrid-music-events/`
- **Landing page** (public): https://github.com/marvin-schumann/vibe-radar-landing
  - Local path: `~/vibe-radar-landing/`
  - Live at: https://vibe-radar-landing.vercel.app

## Current State — What Works

### Data Pipeline
- **Spotify**: Pulls top artists (all time ranges), followed artists, recently played, saved tracks. 114 artists collected. Audio features API is deprecated (403), so we estimate Audio DNA from genre profiles instead.
- **SoundCloud**: Scrapes via internal API (extracted client_id). Pulls likes (175), following (26), reposts. 189 artists collected. Username: `marvin-schumann-794354612`.
- **Combined**: 300 unique artists, 3 overlap (Funk Tribu, 2HOT2PLAY, Marlon Hoffstadt).

### Event Scraping
- **Resident Advisor**: GraphQL API, Madrid area ID = **41**. Scrapes up to 365 days. ~412 events.
- **Songkick**: Web scraping (JSON-LD), Madrid metro area ID = **28755**. ~193 events.
- **Bandsintown**: REST API, artist-based lookup. Found 0 Spain events for these artists (common for underground DJs).
- **Total**: 582 unique events, date range Mar 2026 → Nov 2026.

### Matching
- **Exact matching**: Fuzzy string matching via `thefuzz` (threshold 88%, 100% for short names). Strips DJ/MC/The prefixes.
- **Vibe matching**: Keyword-based genre matching (techno, house, trance, hardcore, etc.) against event names and artist names.
- **Results**: 8 exact matches + 36 vibe matches = 44 total.

### Exact Matches Found
| Date | Artist | Venue | Source |
|------|--------|-------|--------|
| Mar 19 | southstar | Mondo | Spotify |
| Mar 26 | Montee | Sala Nazca | Spotify |
| Mar 27 | Angerfist | IFEMA (BlackWorks) | Spotify |
| Mar 29 | Hans Zimmer | Movistar Arena | Spotify |
| Apr 11 | mischluft | Terraza Jowke | Spotify |
| Apr 18 | Reinier Zonneveld | Fabrik (VERKNIPT) | Spotify |
| May 14 | DVAID | TBA (INSANE Festival) | SoundCloud |
| Jun 27 | Somewhen | Fabrik (Carl Cox anniv.) | SoundCloud |

### Web App (localhost:8000)
- **FastAPI** backend serving from snapshot JSON files (no DB yet)
- **Dashboard**: Dark-themed single-page app with Chart.js visualizations
  - Genre distribution bar chart
  - Audio DNA radar chart (estimated from genres)
  - Source breakdown (Spotify vs SoundCloud doughnut)
  - Event cards with filter tabs (All/Exact/Vibe)
  - Confidence bars, match reasons, external links
- **PDF Export**: `/api/report/pdf` — dark-themed 4-page report via fpdf2
- **Floating buttons**: Magenta (PDF) + Cyan (Refresh)
- Runs via: `cd ~/madrid-music-events && source .venv/bin/activate && uvicorn src.api.main:app --reload --port 8000`

### Landing Page (Vercel)
- Static HTML, dark theme matching the app
- Waitlist email form (needs Formspree ID to actually collect emails — currently saves to localStorage)
- Sections: Hero, How It Works, Features, Cities, Final CTA
- Auto-deploys from GitHub on push

## Architecture

```
madrid-music-events/
├── src/
│   ├── config.py                  # Pydantic settings from .env
│   ├── models.py                  # Artist, Event, Match, TasteProfile, AudioFeatures
│   ├── collectors/
│   │   ├── spotify.py             # SpotifyCollector (spotipy OAuth)
│   │   ├── soundcloud.py          # SoundCloudCollector (httpx scraping)
│   │   └── events/
│   │       ├── resident_advisor.py  # RA GraphQL (area 41)
│   │       ├── bandsintown.py       # Bandsintown REST
│   │       └── songkick.py         # Songkick (metro 28755)
│   ├── matching/
│   │   ├── exact.py               # ExactMatcher (thefuzz)
│   │   └── vibe.py                # VibeMatcher + build_taste_profile()
│   ├── visualization/
│   │   └── taste_profile.py       # TasteVisualizer (Chart.js data generators)
│   ├── api/
│   │   └── main.py                # FastAPI app (routes, PDF export, snapshot loading)
│   └── web/
│       └── templates/
│           └── index.html         # Dashboard (1400+ lines, inline CSS/JS, Chart.js)
├── scripts/
│   └── run_match.py               # CLI runner with rich output
├── data/                          # .gitignored, contains personal snapshots
│   ├── spotify_snapshot.json      # 114 artists + estimated audio features
│   ├── soundcloud_snapshot.json   # 189 artists
│   ├── madrid_events.json         # 582 events + 8 matches
│   └── artist_names.json          # Combined artist name list
├── .env                           # SOUNDCLOUD_USERNAME set
├── .env.example
├── pyproject.toml
└── CLAUDE.md
```

## What's NOT Built Yet

### Phase 1: Database Migration
- Replace JSON snapshot files with Supabase PostgreSQL
- Schema designed (see plan below) but not created
- Key tables: profiles, connected_accounts, user_artists, events, user_matches, cities

### Phase 2: Authentication
- Supabase Auth (email/password + optional Google)
- Spotify as connected account (OAuth per user, tokens in DB)
- SoundCloud as connected account (username entry)
- Per-user dashboards

### Phase 3: Background Jobs + Notifications
- APScheduler for automated scraping every 6 hours
- Match recomputation after scrapes
- Weekly email digest via Resend
- Instant exact-match alerts (pro feature)

### Phase 4: Multi-City
- City selector in settings
- Cities table with RA area IDs + Songkick metro IDs
- Known IDs: Madrid=41, Berlin=34, Barcelona=44, London=13, Amsterdam=29

### Phase 5: Monetization
- Lemon Squeezy for €2/month subscriptions (handles EU VAT)
- Free: 1 city, exact matches only, Spotify only
- Pro: 5 cities, exact+vibe, SoundCloud, instant alerts, clean PDF

### Phase 6: React Frontend (optional)
- Next.js on Vercel, only if product-market fit validated

## Key Technical Notes

- **Spotify audio features API is dead** (403 Forbidden since late 2024). Audio DNA is estimated from genre profiles using a genre→features mapping table.
- **Spotify Extended Quota**: Need to apply before going public (dev mode = 25 users max). Submit at developer.spotify.com.
- **RA area IDs** are not documented — found via community GitHub repos. Madrid = 41.
- **Songkick API** is not accepting new key applications. We scrape their website (JSON-LD in HTML).
- **SoundCloud client_id** is extracted from their JS bundles at runtime. Fragile but works.
- **Event lineups** are typically announced 2-4 weeks before the event. Re-scraping weekly catches new matches.
- The matching engine (`ExactMatcher`, `VibeMatcher`) is stateless and clean — no changes needed for multi-user.

## Environment Setup

```bash
cd ~/madrid-music-events
source .venv/bin/activate

# Run the web app
uvicorn src.api.main:app --reload --port 8000

# Run CLI matcher
python -m scripts.run_match

# Re-scrape events (run this periodically)
# Just ask Claude to "re-run the Madrid event scraper and update matches"
```

### .env variables needed
```
SOUNDCLOUD_USERNAME=marvin-schumann-794354612
SPOTIFY_CLIENT_ID=         # needed for OAuth flow (not yet set up)
SPOTIFY_CLIENT_SECRET=     # needed for OAuth flow (not yet set up)
```

## Dependencies

Python 3.11+, key packages: spotipy, httpx, beautifulsoup4, pydantic, pydantic-settings, fastapi, uvicorn, thefuzz, plotly, loguru, rich, fpdf2, jinja2

## Waitlist Landing Page

- **URL**: https://vibe-radar-landing.vercel.app
- **Repo**: https://github.com/marvin-schumann/vibe-radar-landing
- **Status**: Deployed, needs Formspree form ID to collect emails
- **To connect Formspree**: Edit `index.html`, set `FORMSPREE_URL = 'https://formspree.io/f/YOUR_ID'`, push to GitHub
- **Custom domain**: Run `vercel domains add viberadar.app` after purchasing domain
