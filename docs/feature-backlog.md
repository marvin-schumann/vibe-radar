# Frequenz — Feature Backlog

## Current State (2026-04-04)
- **Artists:** ~350 from Spotify top artists + 21 followed. Clean list, user recognises all.
- **Events:** 100 from Resident Advisor Madrid. Title parsing extracts extra artist names.
- **Matching:** Fuzzy matching with partial_ratio gated to 8+ char names. Threshold 85.
- **SoundCloud:** Connected, ~138 artists from likes.
- **UI:** Revamped on `ui-revamp` branch. Global platform toggle in header. Artists panel (togglable).
- **Known limitation:** Spotify dev apps no longer return genre data — blocks genre filtering until Extended Quota approved.

---

## Taste Card Features (from research)

Ranked by: psychological resonance, shareability, technical feasibility, differentiation.

| Rank | Feature | What | Feasibility | Builds on |
|------|---------|------|-------------|-----------|
| 1 | **Listener Archetype** | Assign one of 7 "taste tribes" (Warehouse Monk, Sonic Archaeologist, Fog Machine Philosopher, Strobe Nomad, Dawn Chaser, Bass Templar, Circuit Bender) based on genre distribution, popularity, and listening patterns | High | artist.genres[], artist.popularity, top_artists time ranges |
| 2 | **Taste Evolution Radar** | Show how your taste is shifting — compare short_term vs long_term genre vectors. "You're drifting from melodic house toward dark techno" | High | top_artists per time_range |
| 3 | **Underground Depth Score** | 0-100 score from inverse avg artist popularity. "You're deeper than 94% of listeners" | High | artist.popularity only |
| 4 | **Tempo Fingerprint** | BPM distribution chart — "You're a 132 BPM person" with single peak vs flat distribution | Medium | Needs Cyanite API or genre-based BPM inference |
| 5 | **Scene: Home City** | Match your taste to a city scene. "Your sound is 78% Berlin, 15% London" | High | Genre tags (city-coded: "berlin techno", "uk bass") |
| 6 | **Cross-Genre Bridge** | Detect rare genre combinations. "You bridge minimal techno and jazz — only 3% of listeners do that" | High | artist.genres[] + population baseline |
| 7 | **Event DNA Match Card** | Shareable card showing why a specific event matches you. "91% taste alignment with Fabric London" | High | Genre overlap + popularity tier |
| 8 | **DJ Twin Match** | "Your taste is closest to Peggy Gou's" — cosine similarity against DJ reference profiles from public Spotify playlists | High | top_artists, genres, popularity |
| 9 | **Listening Age** | Median release year of your top tracks. "Your ears live in 2019" | High | album.release_date metadata |
| 10 | **Dancefloor vs Headphones** | Ratio of club-oriented vs ambient listening. Genre-based proxy for deprecated danceability | Medium | Genre inference |

### Taste Tribe Definitions (from research)

- **Warehouse Monk** — "Devotion to the 4/4 sacrament." Techno loyalist. Narrow BPM, low popularity, low genre diversity.
- **Sonic Archaeologist** — "Digging is the destination." Crate digger. Wide genre spread across decades, very low popularity.
- **Fog Machine Philosopher** — "Dancing is thinking with your body." Cerebral. Experimental, leftfield, variable tempo.
- **Strobe Nomad** — "The party is the pilgrimage." Festival circuit. House/disco/afro house, moderate popularity, event-driven.
- **Dawn Chaser** — "The comedown is the peak." Ambient/downtempo. Low energy, daytime listening patterns.
- **Bass Templar** — "Sub-frequencies are scripture." DnB/dubstep/jungle. High BPM, high energy.
- **Circuit Bender** — "The patch cable is the instrument." Producer-listener. IDM/modular/experimental, craft-focused.

---

## Competitive Intelligence (from brand research)

### Key Gaps We Can Exploit
- **No competitor connects taste identity to event recommendations.** This is genuine white space.
- **RA has weak algorithmic discovery** — no cold-start, no "you might like", purely follow-graph.
- **DICE post-Fever acquisition** is going commercial, losing underground credibility.
- **Songkick lost Spotify** partnership (2024), acquired by Suno (AI music) — uncertain future.
- **Bandsintown** has no underground/niche events and terrible UX ("the concert app everyone uses but nobody loves").

### Madrid Market Insights
- Core demo: 22-35, community-oriented, smaller than Berlin/London but more intimate
- Key venues: Mondo Disko, Fabrik, Stardust, Siroco
- Key labels: Semantica Records, PoleGroup (international recognition)
- Discovery: RA primary, Instagram critical, Telegram groups for underground, XCEED for mainstream
- Monthly spend: €120-300 for active fans (4 events/month)
- Barcelona generates 26% of Spain's music event revenue, surpassing Madrid — opportunity to differentiate

### Brand Name: "Frequenz"
- frequenz.com taken (Berlin energy company, €23M raised)
- frequenz.fm taken (German radio directory)
- @frequenz on Instagram taken (personal account)
- High cultural fit in Germany, moderate in Spain/UK
- Need alternative domains: getfrequenz.com, frequenz.app, frequenz.club
- Trademark risk: moderate (same word, different industry class)

---

## Build Next

### 1. Shareable Taste Card (MVP)
Start with 3 simplest high-impact features:
- **Underground Depth Score** (just artist.popularity — can build today)
- **Listener Archetype** (genre distribution → tribe assignment — blocked on Spotify genres for dev apps)
- **Scene: Home City** (same blocker)

**Unblocked now:** Underground Depth Score. Build as a visual card with share button.

### 2. Fix SoundCloud Artist Quality
Parse actual track artist name from SC track metadata instead of uploader username.

### 3. Deploy to Railway (Phase 4)
Code ready. Manual steps for Marvin.

### 4. Genre Filter
Blocked until Spotify Extended Quota. Once approved: auto-filter to electronic genres, enable Listener Archetype + Scene City.

### 5. Lemon Squeezy Payments (Phase 5)
Founding Member CTA already on pending page.

### 6. Automated Scraping + Email Digests (Phase 6)
APScheduler + Resend API.

---

## Parked / Won't Do
- [x] ~~Bandsintown~~ — doesn't have underground events
- [x] ~~Genre filter~~ — blocked until Spotify Extended Quota
- [x] ~~Audio features~~ — deprecated by Spotify Nov 2024
- [ ] Cyanite API — add later if tempo fingerprint proves essential (costs money)
- [ ] MusicBrainz/Discogs enrichment — add for micro-genre precision once MVP is validated
