# Frequenz API — Operational Notes

Quick-reference for the FastAPI app: cron endpoints, scheduled jobs, and
the Monday Drop retention ritual.

## Monday Drop

The Monday Drop is Frequenz's core retention feature. Every Monday at
08:00 local time, every eligible user receives an email with 5 ranked
events for the upcoming week, each tagged with a match score and a
one-sentence reason.

**Module:** `src/api/monday_drop.py`
**Template:** `src/web/templates/emails/monday_drop.html`
**Endpoint:** `POST /api/cron/monday-drop`

### Eligibility

A user receives a Monday Drop when:

- `profiles.is_approved = true`
- `profiles.email_verified != false` (defaults to eligible if column is
  missing — schema currently has no `email_verified` column)
- `profiles.email_opt_in != false` (same — future-proof)

### Triggering manually

Local dry run (renders HTML to `/tmp/monday-drop-preview.html`):

```bash
python scripts/test_monday_drop.py
open /tmp/monday-drop-preview.html
```

Send a preview drop to `hello@frequenz.live` via Brevo:

```bash
python scripts/test_monday_drop.py --send
```

Full pipeline for one real user (authenticates against Supabase, runs
the matcher, then sends):

```bash
python scripts/test_monday_drop.py --user-id <supabase-user-uuid> --send
```

Trigger the batch endpoint directly (localhost):

```bash
curl -X POST http://localhost:8000/api/cron/monday-drop \
     -H "X-Cron-Secret: $ADMIN_SECRET_KEY"
```

### Scheduling the cron

Two acceptable approaches — pick whichever the deploy target supports.

**GitHub Actions** (`.github/workflows/monday-drop.yml`):

```yaml
name: monday-drop
on:
  schedule:
    - cron: "0 6 * * 1"  # 08:00 Europe/Madrid = 06:00 UTC in summer,
                         # 07:00 UTC in winter. Use 06:00 UTC for CEST,
                         # switch twice a year or use two entries.
jobs:
  fire:
    runs-on: ubuntu-latest
    steps:
      - name: Fire Monday Drop
        run: |
          curl -fsS -X POST https://app.frequenz.live/api/cron/monday-drop \
               -H "X-Cron-Secret: ${{ secrets.ADMIN_SECRET_KEY }}"
```

**Coolify scheduled task** (preferred — no timezone drift, runs inside
the app container):

```
Schedule:  0 8 * * 1
Timezone:  Europe/Madrid
Command:   curl -fsS -X POST http://localhost:8000/api/cron/monday-drop \
                -H "X-Cron-Secret: $ADMIN_SECRET_KEY"
```

### Rate limits

Brevo's **free tier caps at 300 transactional emails per day**. The
Monday Drop batch runs sequentially and logs failures rather than
aborting — if the waitlist grows past 300 approved users we either:

1. Upgrade the Brevo plan (25K/mo starts around €19/mo), or
2. Split the batch across two days (Mon 08:00 + Tue 08:00), or
3. Move transactional sends to AWS SES (~$0.10 / 1000 mails).

Monitor `sent` vs `failed` in the endpoint response and in the loguru
log line `monday_drop batch complete`.

### Tagging for analytics

Every sent email is tagged with `["monday_drop", "week-WW"]` (and
`"monday_drop_fallback"` if the user had no events this week). Filter
by tag in the Brevo dashboard to measure open rate / click rate per
week and compare cohorts.

### Failure case: no events this week

If a user has zero events inside the 7-day window (wrong city, small
scene, pipeline hiccup), they get a fallback email thanking them and
telling them we're expanding coverage. This is intentional — a silent
skip is a retention hole.

## Other cron-style endpoints

- `GET /api/scheduler/status` — background event scraper status
  (authenticated, not a cron target)
- The background event scraper itself runs in-process via APScheduler
  (`src/api/scheduler.py`) and does not need external triggering.
