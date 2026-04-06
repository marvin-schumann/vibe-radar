"""Background event scraping scheduler using APScheduler."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from src.config import settings

DATA_DIR = Path(__file__).parent.parent.parent / "data"

# Lock to prevent concurrent scraping (background + manual refresh)
_scrape_lock = asyncio.Lock()

# Status tracking for the /api/scheduler/status endpoint
_scheduler_status: dict[str, Any] = {
    "last_run": None,
    "next_run": None,
    "events_collected": None,
    "last_error": None,
}

scheduler = AsyncIOScheduler()


def get_scheduler_status() -> dict[str, Any]:
    """Return current scheduler status for the status endpoint."""
    # Update next_run from the live scheduler job
    jobs = scheduler.get_jobs()
    if jobs:
        next_run = jobs[0].next_run_time
        _scheduler_status["next_run"] = next_run.isoformat() if next_run else None
    else:
        _scheduler_status["next_run"] = None
    _scheduler_status["running"] = scheduler.running
    return _scheduler_status


async def scrape_events() -> int:
    """Collect events from all sources and persist to data/madrid_events.json.

    Returns the number of events collected. Uses a lock to prevent
    concurrent runs (e.g. background job + manual refresh at the same time).
    """
    if _scrape_lock.locked():
        logger.info("Event scraping already in progress — skipping")
        return -1

    async with _scrape_lock:
        from src.collectors.events.resident_advisor import ResidentAdvisorCollector
        from src.collectors.events.songkick import SongkickCollector

        logger.info("Background event scrape starting")
        _scheduler_status["last_error"] = None
        days = settings.days_ahead
        all_events = []

        # Collect from sources that don't require user-specific artist lists
        ra_collector = ResidentAdvisorCollector()
        sk_collector = SongkickCollector()

        results = await asyncio.gather(
            ra_collector.collect_events(days_ahead=days),
            sk_collector.collect_events(days_ahead=days),
            return_exceptions=True,
        )

        source_names = ["Resident Advisor", "Songkick"]
        for source_name, result in zip(source_names, results):
            if isinstance(result, Exception):
                logger.warning("Background scrape — {} failed: {}", source_name, result)
                _scheduler_status["last_error"] = f"{source_name}: {result}"
            else:
                all_events.extend(result)
                logger.info("Background scrape — {}: {} events", source_name, len(result))

        # Persist to disk
        collected_at = datetime.now(tz=timezone.utc).isoformat()
        events_payload = {
            "collected_at": collected_at,
            "events": [e.model_dump(mode="json") for e in all_events],
        }

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        events_path = DATA_DIR / "madrid_events.json"
        with open(events_path, "w") as f:
            json.dump(events_payload, f, default=str)

        # Update in-memory snapshot cache (imported lazily to avoid circular import)
        from src.api.main import _cache

        _cache["events_snapshot"] = events_payload
        _cache["last_refresh"] = collected_at

        _scheduler_status["last_run"] = collected_at
        _scheduler_status["events_collected"] = len(all_events)

        logger.info("Background event scrape complete: {} events persisted", len(all_events))
        return len(all_events)


async def _scheduled_scrape_job() -> None:
    """Wrapper called by APScheduler — catches all errors so the job never crashes."""
    try:
        await scrape_events()
    except Exception as exc:
        logger.error("Background event scrape crashed: {}", exc)
        _scheduler_status["last_error"] = str(exc)


def start_scheduler() -> None:
    """Add the recurring event scrape job and start the scheduler."""
    interval_hours = settings.event_scrape_interval_hours
    scheduler.add_job(
        _scheduled_scrape_job,
        "interval",
        hours=interval_hours,
        id="event_scrape",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("Event scraper scheduled every {} hours", interval_hours)


def stop_scheduler() -> None:
    """Shut down the scheduler gracefully."""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Event scraper scheduler shut down")
