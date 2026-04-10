"""Preview + smoke-test the Monday Drop email template.

Usage
-----

    python scripts/test_monday_drop.py             # render-only preview
    python scripts/test_monday_drop.py --send      # also send to hello@frequenz.live

Renders the email to ``/tmp/monday-drop-preview.html`` so you can
``open`` it in a browser and sanity-check the layout before pointing
the cron at production.

Uses hard-coded fake data by default so no Supabase / Brevo calls are
required for a pure render check. Pass ``--user-id UUID`` to pull a
real user's data through the full pipeline.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger

# Ensure project root is on sys.path when run directly
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.api.monday_drop import (  # noqa: E402
    compute_weekly_drop_for_user,
    render_monday_drop_email,
    send_monday_drop_to_user,
)
from src.integrations import brevo as brevo_integration  # noqa: E402


PREVIEW_PATH = Path("/tmp/monday-drop-preview.html")


def _fake_drop_data() -> dict:
    """A hand-rolled drop payload that mirrors the shape produced by
    :func:`compute_weekly_drop_for_user` so the template renders in
    isolation."""
    base = datetime.now(tz=timezone.utc) + timedelta(days=2)
    return {
        "user_id": "preview-user",
        "has_events": True,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "week_tag": f"week-{datetime.now(tz=timezone.utc).isocalendar().week:02d}",
        "cards": [
            {
                "name": "Fabric presents: Peggy Gou",
                "venue": "Fabric London",
                "date": base.strftime("%a, %d %b"),
                "score": 94,
                "reason": "You've liked 4 tracks from Peggy Gou in the last 60 days.",
                "ticket_url": "https://ra.co/events/1234567",
                "artists": ["Peggy Gou", "HAAi"],
            },
            {
                "name": "Mutek Madrid — Night 2",
                "venue": "Sala Apolo",
                "date": (base + timedelta(days=1)).strftime("%a, %d %b"),
                "score": 89,
                "reason": "87% taste overlap with the Mutek 2024 closing set.",
                "ticket_url": "https://wegow.com/events/mutek-madrid",
                "artists": ["Jlin", "Actress"],
            },
            {
                "name": "Hyperdub 20 — DJ Haram b2b Loraine James",
                "venue": "Nitsa",
                "date": (base + timedelta(days=2)).strftime("%a, %d %b"),
                "score": 82,
                "reason": "Loraine James is in your top 10 SoundCloud plays.",
                "ticket_url": "https://ra.co/events/1234568",
                "artists": ["DJ Haram", "Loraine James"],
            },
            {
                "name": "Nitsa: Theo Parrish — All Night Long",
                "venue": "Nitsa",
                "date": (base + timedelta(days=3)).strftime("%a, %d %b"),
                "score": 76,
                "reason": "You played 'Falling Up' 12 times in March.",
                "ticket_url": "https://ra.co/events/1234569",
                "artists": ["Theo Parrish"],
            },
            {
                "name": "Aphex Twin — Warehouse Project",
                "venue": "Depot Mayfield",
                "date": (base + timedelta(days=4)).strftime("%a, %d %b"),
                "score": 71,
                "reason": "Your taste profile sits in the IDM/breaks cluster.",
                "ticket_url": "https://ra.co/events/1234570",
                "artists": ["Aphex Twin", "Nina Kraviz"],
            },
        ],
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description="Preview the Monday Drop email")
    parser.add_argument(
        "--send",
        action="store_true",
        help="Also send the rendered email to hello@frequenz.live via Brevo",
    )
    parser.add_argument(
        "--user-id",
        type=str,
        default=None,
        help="Pull real data for this Supabase user_id instead of using fake data",
    )
    parser.add_argument(
        "--to",
        type=str,
        default="hello@frequenz.live",
        help="Override the recipient for --send (default: hello@frequenz.live)",
    )
    args = parser.parse_args()

    if args.user_id:
        logger.info("Computing Monday Drop for real user {}", args.user_id)
        drop_data = await compute_weekly_drop_for_user(args.user_id)
    else:
        logger.info("Rendering with hard-coded fake drop data")
        drop_data = _fake_drop_data()

    user_payload = {
        "email": args.to,
        "profile": {"character_name": "Bunker Bear"},
    }
    subject, html_body = render_monday_drop_email(user_payload, drop_data)

    PREVIEW_PATH.write_text(html_body, encoding="utf-8")
    logger.info("Preview written: {}", PREVIEW_PATH)
    logger.info("Subject line: {}", subject)
    print(f"\nopen {PREVIEW_PATH}\n")

    if args.send:
        if args.user_id:
            logger.info("Sending real Monday Drop to user {}", args.user_id)
            await send_monday_drop_to_user(args.user_id)
        else:
            logger.info("Sending preview drop to {}", args.to)
            await brevo_integration.send_transactional_email(
                to_email=args.to,
                to_name="Monday Drop Preview",
                subject=subject,
                html_content=html_body,
                tags=["monday_drop", "preview", drop_data["week_tag"]],
            )
        logger.info("Sent.")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
