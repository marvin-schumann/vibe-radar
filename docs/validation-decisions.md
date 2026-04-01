# Vibe Radar — Validation Decisions

*Last updated: 2026-04-01*

---

## What We're Validating

1. Do electronic music fans in Madrid have a real, frequent pain point finding events?
2. Will they pay €4.99/month for taste-based matching?
3. Is artist-match sufficient, or is vibe/genre matching the actual differentiator?

---

## Build Priority (Validation Sprint)

### Tier 1 — This Week

| # | Feature | Why |
|---|---------|-----|
| 1 | Waitlist form: add 5 validation questions | Free research with every signup |
| 2 | Spotify OAuth → event matches in <60 seconds | Core aha moment. Nothing else matters without this. |
| 3 | RA Madrid event scraper (if not running) | Data without which matching is empty |
| 4 | Founding Member Stripe link (€20 lifetime or €4.99 pre-launch) | 5 paying users = more signal than 500 signups |
| 5 | Post-match NPS prompt (1 question, 24h after first match) | PMF benchmark: target 40%+ "Very disappointed" |

### Tier 2 — First Month

| # | Feature | Why |
|---|---------|-----|
| 6 | Genre tag at waitlist signup | Enables segmented email campaigns |
| 7 | Funnel analytics: 4 events only | signup → spotify connected → first match → return visit |
| 8 | Weekly Thursday digest email | Core Pro feature; identifies conversion candidates |

### Do Not Build Yet

- SoundCloud OAuth
- Multi-city support
- PDF export
- Advanced vibe/genre matching beyond artists
- Referral system

---

## Waitlist Form Questions

1. City + how often you go to club/live electronic events per month
2. How did you find out about the last 3 events you attended? (open text)
3. Biggest frustration with current event discovery (open text)
4. What apps/sites/tools do you use now to find events? (open text)
5. What would you pay per month? — €0 / €2–3 / €4–5 / €6–10 / €10+

---

## Metrics: 30/60/90 Days

| Phase | Metric | Target |
|-------|--------|--------|
| Day 0–30 | Waitlist signups, form completion rate | 100+ signups, >60% completion |
| Day 30–60 | Spotify connect rate, time-to-first-match, NPS | >50% activation, NPS >30 |
| Day 60–90 | W2 retention, PMF survey score, paying users | 30%+ return week 2, 1+ paying user |

**Single most important signal:** Has anyone paid?

---

## User Acquisition — Ordered by Signal Quality

1. Direct DMs to RA Madrid / Boiler Room commenters (highest intent)
2. Madrid DJ and promoter Instagram followers (Fabrik, Mondo, Weekend Beach)
3. Reddit: r/electronicmusic, r/techno, r/Madrid — post as researcher, not founder
4. Discord: genre-specific servers, #tools-or-resources channels
5. Facebook Groups: Fiestas Madrid, Techno Madrid, Electronic Music Madrid

---

## PMF Test

Send to first 40+ active users:

> "How would you feel if you could no longer use Vibe Radar?"
> Very disappointed / Somewhat disappointed / Not at all disappointed

**Decision rule:** <40% "Very disappointed" → talk to that minority, find what they value, optimize only for them. Do not expand scope.

---

## Strategic Decisions

- **Madrid only until Madrid is saturated.** Multi-city is a Pro feature to build after PMF, not before.
- **Artist matching ships first.** Vibe/genre matching only if users say artist matching isn't good enough.
- **Manual concierge before automation.** For first 10 beta users, manually build their recommendations (Spotify Wrapped + RA search). Validate the value prop before automating it.
- **Content as acquisition.** A "Madrid weekend electronic picks" Instagram or newsletter, run manually, is both user research and the top-of-funnel.
