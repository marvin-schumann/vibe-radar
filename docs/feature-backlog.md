# Frequenz — Feature Backlog

## Current State (2026-04-04)
- **Artists:** ~350 from Spotify top artists + 21 followed. Clean list, user recognises all.
- **Events:** 100 from Resident Advisor Madrid. Title parsing extracts extra artist names.
- **Matching:** Fuzzy matching with partial_ratio gated to 8+ char names. Threshold 85.
- **SoundCloud:** Connected but disabled (usernames ≠ artist names, no genre data).
- **UI:** Revamped on `ui-revamp` branch (Bebas Neue / Space Mono / DM Sans, lime+orange palette).
- **Known limitation:** Spotify dev apps no longer return genre data, so genre filtering is impossible until Extended Quota is approved.

---

## Build Next

### 1. Artists Tab
Show all collected artists in a dedicated tab/page on the dashboard:
- Grouped by platform (Spotify / SoundCloud)
- Artist profile image (from Spotify API — already collected)
- Name, genres (if available), popularity
- Lets users audit what the app is working with and spot issues

### 2. Platform Toggle on Matches
Filter bar on matched events: "All / Spotify only / SoundCloud only"
- Already have the filter tab pattern in the UI
- Useful once SoundCloud is re-enabled

### 3. Re-enable SoundCloud (properly)
Current issue: SC likes return uploader usernames, not artist names.
Fix options:
- Only use SC followed artists (explicit follow = real signal)
- Parse actual track artist name from SC track metadata instead of uploader username
- Add a manual "add artist" feature as a fallback

### 4. Genre Focus / Electronic Filter
Once Spotify Extended Quota is approved (returns genre data):
- Auto-filter to electronic genres only
- Settings page to choose genre focus (techno, house, trance, all electronic, etc.)
- Until then: users see all top artists including pop/rock

---

## Ship Soon

### 5. Deploy to Railway (Phase 4)
Code is ready (Procfile + nixpacks.toml committed). Manual steps:
- Create Railway account → connect GitHub repo
- Add env vars from .env.example
- Set APP_HOST + SPOTIFY_REDIRECT_URI
- Add callback URL to Spotify Developer Dashboard

### 6. Shareable Taste Card
Wrapped-style visual: top genres, audio DNA radar, "crate digger score", tempo fingerprint.
Research prompt written (pending execution in separate chat).
Uses canvas-design skill.

### 7. Lemon Squeezy Payments (Phase 5)
- Founding Member CTA already on pending page (placeholder link)
- Need: webhooks.py, Pro tier gating (lock icons on vibe matches)

### 8. Automated Scraping + Email Digests (Phase 6)
- APScheduler: scrape RA every 6h, recompute matches, Monday 9am digest
- Resend API for email

---

## Ideas / Research

- [ ] "Your taste is like X DJ" — personality-test-style archetype matching
- [ ] Taste evolution tracking — how your short_term vs long_term profile changes
- [ ] "Crate digger score" — average artist popularity (lower = more underground)
- [ ] Cross-genre bridge detection ("you like both minimal techno and jazz — that's rare")
- [ ] Darkness index, tempo fingerprint, dancefloor vs headphones ratio
- [ ] Celebrity taste comparison (needs curated reference profiles)

---

## Won't Do / Parked
- [x] ~~Bandsintown~~ — checked, doesn't have underground/RA-style events. Not useful.
- [x] ~~Genre filter~~ — blocked until Spotify Extended Quota (genres not returned for dev apps)
- [x] ~~SoundCloud liked tracks for matching~~ — usernames not artist names, adds noise
