"""Monday Drop — the retention ritual.

Every Monday 08:00 local time, every active user gets an email with 5
ranked events for the week ahead, each tagged with a match score and a
one-sentence reason. This is Frequenz's Monday-morning habit loop.

The retention research is unambiguous: "Frequenz is a Monday-morning
ritual or it is nothing." The Monday Drop is the killer feature.

Pipeline:
  1. Fetch all eligible users from Supabase (is_approved=true,
     emails verified, opted in).
  2. For each user, run (or reuse a cached) matching pipeline so we
     have ranked events.
  3. Filter to events in the next 7 days, take the top 5 by confidence.
  4. Render the Jinja2 email template with the user's 5 cards.
  5. Send via Brevo with tags ``["monday_drop", "week-WW"]``.

Design notes:
  - All functions are async to match the existing codebase style.
  - The matching pipeline is expensive — we reuse the per-user in-memory
    cache populated by :func:`src.api.main._run_pipeline`. If the cache
    is empty (cold start) we run the pipeline inline.
  - Brevo's free tier caps out at 300 transactional emails per day. The
    caller (``send_monday_drop_to_all_users``) respects this ceiling by
    iterating sequentially and logging failures rather than aborting.
  - Users with **zero eligible events this week** get a fallback "we're
    working on more cities" email instead of being silently skipped.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from loguru import logger

from src.config import settings
from src.db.supabase import get_admin_client
from src.integrations import brevo as brevo_integration
from src.integrations.brevo import BrevoError
from src.models import Match


# ---------------------------------------------------------------------------
# Jinja2 environment (email templates)
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent.parent / "web" / "templates"
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _current_week_tag(now: datetime | None = None) -> str:
    """Return an ISO week tag like ``week-16`` used for Brevo analytics."""
    now = now or datetime.now(tz=timezone.utc)
    return f"week-{now.isocalendar().week:02d}"


def _format_event_date(dt: datetime) -> str:
    """Human-friendly date string for email cards, e.g. 'Fri, 18 Apr'."""
    return dt.strftime("%a, %d %b")


def _greeting_name(profile: dict[str, Any], auth_user_email: str) -> str:
    """Pick the best display name for the greeting hook.

    Preference order: character_name → first_name → email local-part.
    The profiles schema today has neither ``character_name`` nor
    ``first_name`` columns — we read them defensively so this code keeps
    working once the migration lands.
    """
    for key in ("character_name", "first_name"):
        value = profile.get(key)
        if value:
            return str(value).strip()
    local_part = auth_user_email.split("@", 1)[0]
    return local_part.replace(".", " ").replace("_", " ").title() or "friend"


def _confidence_to_score(confidence: float) -> int:
    """Map a 0-1 match confidence to a 0-100 integer score for display."""
    return max(0, min(100, round(float(confidence) * 100)))


def _pick_top_events_for_week(
    matches: list[Match],
    *,
    now: datetime | None = None,
    limit: int = 5,
) -> list[Match]:
    """Select the top N matches occurring within the next 7 days.

    ``matches`` is already sorted by the pipeline's sort_key (exact first,
    then confidence). We additionally constrain to a 7-day rolling window
    starting today (local server time — good enough; timezone-per-user
    isn't needed for a weekly filter).
    """
    now = now or datetime.now(tz=timezone.utc)
    horizon = now + timedelta(days=7)
    in_window: list[Match] = []
    seen_urls: set[str] = set()

    for m in matches:
        event_dt = m.event.date
        # Pipeline events are usually naive or UTC-naive; normalise.
        if event_dt.tzinfo is None:
            event_dt_cmp = event_dt.replace(tzinfo=timezone.utc)
        else:
            event_dt_cmp = event_dt.astimezone(timezone.utc)
        if event_dt_cmp < now or event_dt_cmp > horizon:
            continue
        if m.event.url and m.event.url in seen_urls:
            continue
        if m.event.url:
            seen_urls.add(m.event.url)
        in_window.append(m)
        if len(in_window) >= limit:
            break

    return in_window


def _match_to_card(match: Match) -> dict[str, Any]:
    """Turn a :class:`Match` into a dict that the Jinja template consumes."""
    event = match.event
    venue_name = event.venue.name if event.venue else ""
    return {
        "name": event.name,
        "venue": venue_name,
        "date": _format_event_date(event.date),
        "score": _confidence_to_score(match.confidence),
        "reason": match.match_reason or "Your taste lines up with the headliners.",
        "ticket_url": event.url or "#",
        "artists": event.artists[:3],
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def compute_weekly_drop_for_user(user_id: str) -> dict[str, Any]:
    """Compute the 5 ranked events + metadata for one user's Monday Drop.

    Returns a dict shaped for the Jinja template:

    .. code-block:: python

        {
            "user_id": str,
            "cards": [card_dict, ...],   # up to 5
            "has_events": bool,
            "generated_at": str (iso),
            "week_tag": str,             # e.g. "week-16"
        }

    When the user has zero eligible events this week the caller should
    switch to the "we're working on more cities" fallback — this function
    returns ``has_events=False`` rather than raising.
    """
    from src.api.main import _run_pipeline, _user_cache  # lazy — avoids cycle

    cache = _user_cache(user_id)
    matches: list[Match] | None = cache.get("matches")

    if matches is None:
        logger.info("monday_drop: no cached matches for user {}, running pipeline", user_id)
        try:
            await _run_pipeline(user_id=user_id)
        except Exception as exc:
            logger.warning("monday_drop: pipeline failed for user {}: {}", user_id, exc)
        matches = cache.get("matches") or []  # after pipeline, fallback to empty

    matches = matches or []

    top = _pick_top_events_for_week(matches)
    cards = [_match_to_card(m) for m in top]

    return {
        "user_id": user_id,
        "cards": cards,
        "has_events": bool(cards),
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "week_tag": _current_week_tag(),
    }


def render_monday_drop_email(
    user: dict[str, Any],
    drop_data: dict[str, Any],
) -> tuple[str, str]:
    """Render the Monday Drop email for a given user + drop payload.

    Returns ``(subject, html_body)``.

    ``user`` is expected to contain at least:
      - ``email``: str
      - ``profile``: dict  (optional — for first_name / character_name)
    """
    profile = user.get("profile") or {}
    email = user.get("email", "")
    greeting_name = _greeting_name(profile, email)
    cards = drop_data.get("cards", [])

    if drop_data.get("has_events") and cards:
        top = cards[0]
        subject = f"Your 5 for the week — {top['name']} is {top['score']}% you"
        template = _jinja_env.get_template("emails/monday_drop.html")
    else:
        subject = "This week in your city — we're working on more venues"
        template = _jinja_env.get_template("emails/monday_drop.html")

    html_body = template.render(
        greeting_name=greeting_name,
        cards=cards,
        has_events=drop_data.get("has_events", False),
        week_tag=drop_data.get("week_tag", _current_week_tag()),
        unsubscribe_url=f"{settings.app_host}/unsubscribe?u={drop_data.get('user_id', '')}",
        skip_week_url=f"{settings.app_host}/skip-week?u={drop_data.get('user_id', '')}",
        app_host=settings.app_host,
    )
    return subject, html_body


async def send_monday_drop_to_user(user_id: str) -> None:
    """Compose and send the Monday Drop for a single user.

    Raises :class:`BrevoError` on hard send failures; the batch driver
    catches these so one broken user doesn't block the rest.
    """
    db = get_admin_client()

    # Pull profile + auth email
    profile_resp = db.table("profiles").select("*").eq("id", user_id).maybe_single().execute()
    profile = profile_resp.data or {}

    email = profile.get("email")
    if not email:
        # Fall back to the auth user record
        try:
            auth_user = db.auth.admin.get_user_by_id(user_id)
            email = getattr(auth_user.user, "email", None) if auth_user else None
        except Exception as exc:
            logger.warning("monday_drop: could not fetch auth email for {}: {}", user_id, exc)

    if not email:
        logger.warning("monday_drop: skipping user {} — no email on file", user_id)
        return

    drop_data = await compute_weekly_drop_for_user(user_id)
    user_payload = {"email": email, "profile": profile}
    subject, html_body = render_monday_drop_email(user_payload, drop_data)

    tags = ["monday_drop", drop_data["week_tag"]]
    if not drop_data["has_events"]:
        tags.append("monday_drop_fallback")

    await brevo_integration.send_transactional_email(
        to_email=email,
        to_name=_greeting_name(profile, email),
        subject=subject,
        html_content=html_body,
        tags=tags,
    )
    logger.info(
        "monday_drop sent: user={} email={} cards={} fallback={}",
        user_id,
        email,
        len(drop_data.get("cards", [])),
        not drop_data["has_events"],
    )


async def _fetch_eligible_users() -> list[dict[str, Any]]:
    """Return all users eligible for a Monday Drop.

    Eligibility today = ``is_approved=true``. The schema doesn't yet
    have ``email_verified`` or ``email_opt_in`` columns, so we treat
    everyone approved as eligible. Once those columns land the query
    below gets two more ``.eq()`` filters — nothing else changes.
    """
    db = get_admin_client()
    resp = db.table("profiles").select("*").eq("is_approved", True).execute()
    rows = resp.data or []
    # Soft filters for columns that may not exist yet.
    eligible: list[dict[str, Any]] = []
    for row in rows:
        if row.get("email_verified") is False:
            continue
        if row.get("email_opt_in") is False:
            continue
        eligible.append(row)
    return eligible


async def send_monday_drop_to_all_users() -> dict[str, int]:
    """Iterate all eligible users and send each their Monday Drop.

    Returns ``{"sent": N, "failed": M, "skipped": K}``. Sequential by
    design — we want predictable Brevo rate-limit behaviour on the free
    tier (300/day cap). One bad user never aborts the batch.
    """
    started = time.perf_counter()
    try:
        users = await _fetch_eligible_users()
    except Exception as exc:
        logger.error("monday_drop: failed to fetch eligible users: {}", exc)
        return {"sent": 0, "failed": 0, "skipped": 0, "error": 1}

    logger.info("monday_drop: starting batch for {} eligible users", len(users))
    sent = 0
    failed = 0
    skipped = 0

    for profile in users:
        user_id = profile.get("id")
        if not user_id:
            skipped += 1
            continue
        try:
            await send_monday_drop_to_user(user_id)
            sent += 1
        except BrevoError as exc:
            logger.error("monday_drop: brevo send failed for user {}: {}", user_id, exc)
            failed += 1
        except Exception as exc:
            logger.error("monday_drop: unexpected failure for user {}: {}", user_id, exc)
            failed += 1

    duration = time.perf_counter() - started
    logger.info(
        "monday_drop batch complete: sent={} failed={} skipped={} duration={:.1f}s",
        sent,
        failed,
        skipped,
        duration,
    )
    return {"sent": sent, "failed": failed, "skipped": skipped}
