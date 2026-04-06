# Frequenz — Madrid Music Events Assistant

## Session Startup
At the start of each session, read `NOTES_FROM_TELEGRAM.md` in this directory. The **Pending** section contains tasks/direction Marvin sent via his Telegram chief-of-staff chat while away from the laptop. Work through them top-to-bottom, then move completed items to the **Done** section with date + commit refs.

## What This Is
A Python app that matches your Spotify/SoundCloud music taste to upcoming DJ events in Madrid. Exact artist matches + genre/vibe similarity. Web dashboard with taste visualization.

## Architecture
```
src/
├── config.py                  # Pydantic settings, env vars
├── models.py                  # Shared data models (Artist, Event, Match)
├── collectors/
│   ├── spotify.py             # Spotify Web API via spotipy
│   ├── soundcloud.py          # SoundCloud scraping via httpx + BeautifulSoup
│   └── events/
│       ├── resident_advisor.py  # RA GraphQL API
│       ├── bandsintown.py       # Bandsintown REST API
│       └── songkick.py         # Songkick API
├── matching/
│   ├── exact.py               # Fuzzy artist name matching
│   └── vibe.py                # Genre + audio feature similarity
├── visualization/
│   └── taste_profile.py       # Generate taste visualizations
├── api/
│   └── main.py                # FastAPI app
└── web/
    ├── templates/             # Jinja2 HTML templates
    └── static/                # CSS, JS, images
```

## Conventions
- Python 3.11+, type hints everywhere
- Use httpx for async HTTP, spotipy for Spotify
- Pydantic models for all data structures
- FastAPI for the web layer
- Use `loguru` for logging
- Keep collectors independent — each returns list[Artist] or list[Event]
- Matching engine takes (artists, events) → list[Match]

## Running
```bash
# CLI mode
python -m scripts.run_match

# Web mode
uvicorn src.api.main:app --reload
```

## Environment Variables
- SPOTIFY_CLIENT_ID
- SPOTIFY_CLIENT_SECRET
- SPOTIFY_REDIRECT_URI (default: http://localhost:8888/callback)
- SOUNDCLOUD_USERNAME
- BANDSINTOWN_APP_ID
- SONGKICK_API_KEY (optional)

## Current Status
Phase 1: Building core data pipeline + matching + CLI output
