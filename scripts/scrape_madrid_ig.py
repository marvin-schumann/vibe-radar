#!/usr/bin/env python3
"""Scrape Madrid venue + collective Instagram accounts for event announcements.

Usage:
    python scripts/scrape_madrid_ig.py                # full run, writes JSON
    python scripts/scrape_madrid_ig.py --dry-run      # parse + log, no write
    python scripts/scrape_madrid_ig.py --only mondodisko_madrid,binary_techno

What it does:
    1. Reads src/data/madrid_ig_accounts.yaml
    2. For each enabled account, fetches the latest N posts via instaloader
    3. Filters posts that look like event announcements
    4. Parses each surviving post into a ParsedEvent (name/date/venue/lineup/url)
    5. Dedupes against data/madrid_events.json by (venue, date, first_dj)
    6. Appends new events in the existing schema and rewrites the JSON
    7. Logs a summary: scraped X, extracted Y, deduped Z, errors W

See /tmp/madrid-scraper-impl.md for the ban-mitigation strategy and the
upgrade-to-Apify path.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

# Repo root on sys.path so "src.*" imports work when running as a script
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.collectors.events.instagram_parser import (  # noqa: E402
    ParsedEvent,
    looks_like_event,
    parse_post,
)
from src.integrations.instagram import (  # noqa: E402
    InstagramError,
    InstagramPost,
    PrivateAccountError,
    ProfileNotFoundError,
    RateLimitError,
    fetch_latest_posts,
)

CONFIG_PATH = REPO_ROOT / "src" / "data" / "madrid_ig_accounts.yaml"
EVENTS_PATH = REPO_ROOT / "data" / "madrid_events.json"
DJ_PROFILES_PATH = REPO_ROOT / "src" / "data" / "dj_profiles.json"


# ---------------------------------------------------------------------------
# Config + IO
# ---------------------------------------------------------------------------


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_existing_events() -> dict[str, Any]:
    if not EVENTS_PATH.exists():
        return {"collected_at": None, "events": []}
    with EVENTS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_events(payload: dict[str, Any]) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with EVENTS_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_known_djs() -> list[str]:
    if not DJ_PROFILES_PATH.exists():
        return []
    try:
        with DJ_PROFILES_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError:
        logger.warning(f"Could not parse {DJ_PROFILES_PATH} — continuing without DJ hints")
        return []
    names: list[str] = []
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict) and row.get("name"):
                names.append(str(row["name"]))
    elif isinstance(data, dict):
        names = [k for k in data.keys() if isinstance(k, str)]
    return names


# ---------------------------------------------------------------------------
# Dedupe
# ---------------------------------------------------------------------------


def _dedupe_key(venue: str | None, date_iso: str | None, first_artist: str | None) -> str:
    v = (venue or "").strip().lower()
    d = (date_iso or "")[:10]  # just the date, ignore time
    a = (first_artist or "").strip().lower()
    return f"{v}|{d}|{a}"


def build_existing_keys(existing: list[dict[str, Any]]) -> set[str]:
    keys: set[str] = set()
    for e in existing:
        venue_name = (e.get("venue") or {}).get("name") if isinstance(e.get("venue"), dict) else None
        first_artist = e["artists"][0] if e.get("artists") else None
        keys.add(_dedupe_key(venue_name, e.get("date"), first_artist))
    return keys


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def parsed_event_to_dict(pe: ParsedEvent) -> dict[str, Any]:
    """Match the existing madrid_events.json schema exactly."""
    date_str = pe.date.replace(microsecond=0).isoformat() if pe.date else None
    venue_block = None
    if pe.venue_name:
        venue_block = {
            "name": pe.venue_name,
            "city": "Madrid",
            "address": None,
            "url": None,
            "latitude": None,
            "longitude": None,
        }
    normalized = [a.lower().strip() for a in pe.lineup]
    # Truncate description to something reasonable
    description = pe.raw_caption.strip()[:800] if pe.raw_caption else None
    return {
        "name": pe.name,
        "artists": pe.lineup,
        "normalized_artists": normalized,
        "venue": venue_block,
        "date": date_str,
        "end_date": None,
        "url": pe.ticket_url or pe.post_url,
        "image_url": pe.image_url,
        "source": "instagram",
        "genres": [],
        "description": description,
        "price": None,
    }


# ---------------------------------------------------------------------------
# Main run loop
# ---------------------------------------------------------------------------


def run(
    *,
    dry_run: bool,
    only: set[str] | None,
) -> int:
    config = load_config()
    accounts = config.get("accounts", [])
    settings = config.get("settings", {}) or {}

    posts_per_account = int(settings.get("posts_per_account", 15))
    max_requests = int(settings.get("max_requests_per_run", 50))
    min_delay = float(settings.get("min_delay_seconds", 5))
    max_delay = float(settings.get("max_delay_seconds", 15))
    retry_max = int(settings.get("retry_max_attempts", 3))
    retry_base = float(settings.get("retry_base_seconds", 60))

    # Prep venue handle set (for cross-referencing @mentions in collective posts)
    venue_handles = {
        str(a["handle"]).lower()
        for a in accounts
        if a.get("kind") == "venue" and a.get("handle")
    }

    known_djs = load_known_djs()
    logger.info(
        f"Loaded {len(accounts)} accounts | "
        f"{len(venue_handles)} venue handles | "
        f"{len(known_djs)} known DJs"
    )

    existing_payload = load_existing_events()
    existing_events: list[dict[str, Any]] = existing_payload.get("events", [])
    existing_keys = build_existing_keys(existing_events)
    logger.info(f"Existing events in JSON: {len(existing_events)}")

    stats = {
        "accounts_attempted": 0,
        "accounts_succeeded": 0,
        "posts_fetched": 0,
        "posts_event_shaped": 0,
        "events_extracted": 0,
        "events_new": 0,
        "events_deduped": 0,
        "errors": 0,
    }

    new_events: list[dict[str, Any]] = []
    requests_remaining = max_requests

    for account in accounts:
        if requests_remaining <= 0:
            logger.warning("Hit max_requests_per_run budget — stopping")
            break

        handle = str(account.get("handle", "")).strip()
        if not handle:
            continue
        if account.get("disabled"):
            logger.info(f"[skip] @{handle} disabled in config")
            continue
        if only and handle not in only:
            continue

        stats["accounts_attempted"] += 1
        kind = account.get("kind", "unknown")
        default_venue = account.get("default_venue")
        limit = min(posts_per_account, max(1, requests_remaining))

        logger.info(f"[fetch] @{handle} ({kind}) — up to {limit} posts")
        try:
            posts = fetch_latest_posts(
                handle,
                limit=limit,
                min_delay=min_delay,
                max_delay=max_delay,
                retry_max_attempts=retry_max,
                retry_base_seconds=retry_base,
            )
        except PrivateAccountError as exc:
            logger.warning(f"[skip] {exc}")
            stats["errors"] += 1
            continue
        except ProfileNotFoundError as exc:
            logger.warning(f"[skip] {exc}")
            stats["errors"] += 1
            continue
        except RateLimitError as exc:
            logger.error(f"[rate-limit] {exc} — aborting run to protect the IP")
            stats["errors"] += 1
            break
        except InstagramError as exc:
            logger.error(f"[error] @{handle}: {exc}")
            stats["errors"] += 1
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"[unexpected] @{handle}: {exc}")
            stats["errors"] += 1
            continue

        stats["accounts_succeeded"] += 1
        stats["posts_fetched"] += len(posts)
        requests_remaining -= len(posts)

        account_new = _process_posts(
            posts,
            handle=handle,
            default_venue=default_venue,
            venue_handles=venue_handles,
            known_djs=known_djs,
            existing_keys=existing_keys,
            new_events=new_events,
            stats=stats,
        )
        logger.info(
            f"[done] @{handle}: {len(posts)} posts → {account_new} new events "
            f"(budget remaining: {requests_remaining})"
        )

        # Polite sleep between accounts too
        if requests_remaining > 0:
            time.sleep(random.uniform(min_delay, max_delay))

    # Summary
    logger.info(
        "Summary | accounts={accounts_succeeded}/{accounts_attempted} "
        "posts={posts_fetched} event_shaped={posts_event_shaped} "
        "extracted={events_extracted} new={events_new} "
        "deduped={events_deduped} errors={errors}".format(**stats)
    )

    if dry_run:
        logger.info(f"[dry-run] would append {len(new_events)} events — not writing")
        for e in new_events[:10]:
            logger.info(
                f"  - {e['name']} @ {(e['venue'] or {}).get('name')} "
                f"on {e['date']} — {len(e['artists'])} artists"
            )
        return 0

    if new_events:
        existing_events.extend(new_events)
        existing_payload["events"] = existing_events
        existing_payload["collected_at"] = datetime.now(timezone.utc).isoformat()
        save_events(existing_payload)
        logger.info(f"Wrote {len(new_events)} new events to {EVENTS_PATH}")
    else:
        logger.info("No new events to write.")

    return 0 if stats["errors"] == 0 else 1


def _process_posts(
    posts: list[InstagramPost],
    *,
    handle: str,
    default_venue: str | None,
    venue_handles: set[str],
    known_djs: list[str],
    existing_keys: set[str],
    new_events: list[dict[str, Any]],
    stats: dict[str, int],
) -> int:
    added = 0
    for post in posts:
        if not looks_like_event(post.caption):
            continue
        stats["posts_event_shaped"] += 1

        parsed = parse_post(
            post.caption,
            posted_at=post.posted_at,
            default_venue=default_venue,
            known_venue_handles=venue_handles,
            known_djs=known_djs,
            image_url=post.image_url,
            post_url=post.post_url,
        )

        if not parsed.is_complete_enough():
            logger.debug(
                f"  [drop] @{handle} {post.shortcode}: missing "
                f"name={bool(parsed.name)} date={bool(parsed.date)} "
                f"venue={bool(parsed.venue_name)}"
            )
            continue

        stats["events_extracted"] += 1

        event_dict = parsed_event_to_dict(parsed)
        first_artist = event_dict["artists"][0] if event_dict["artists"] else None
        key = _dedupe_key(
            (event_dict["venue"] or {}).get("name"),
            event_dict["date"],
            first_artist,
        )
        if key in existing_keys:
            stats["events_deduped"] += 1
            continue

        existing_keys.add(key)
        new_events.append(event_dict)
        stats["events_new"] += 1
        added += 1

    return added


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse + log but don't write to madrid_events.json",
    )
    p.add_argument(
        "--only",
        type=str,
        default=None,
        help="Comma-separated handles to restrict the run to (for debugging)",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    only = None
    if args.only:
        only = {h.strip().lstrip("@") for h in args.only.split(",") if h.strip()}
    logger.info(
        f"Madrid IG scraper starting | dry_run={args.dry_run} "
        f"only={sorted(only) if only else 'all'}"
    )
    return run(dry_run=args.dry_run, only=only)


if __name__ == "__main__":
    raise SystemExit(main())
