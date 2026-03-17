"""Songkick event collector using their REST API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger

from src.config import settings
from src.models import Event, EventSource, Venue

BASE_URL = "https://api.songkick.com/api/3.0"
MADRID_METRO_AREA_ID = 28755


class SongkickCollector:
    """Collects music events from Songkick's metro-area calendar."""

    def __init__(self) -> None:
        self.api_key = settings.songkick_api_key

    async def collect_events(self, days_ahead: int = 30) -> list[Event]:
        """Fetch upcoming events in the Madrid metro area from Songkick.

        Args:
            days_ahead: Number of days into the future to search.

        Returns:
            List of parsed Event models. Empty list on failure or if no
            API key is configured.
        """
        if not self.api_key:
            logger.warning(
                "Songkick API key not configured — skipping collection. "
                "Set SONGKICK_API_KEY in your .env file."
            )
            return []

        now = datetime.now(tz=timezone.utc)
        end_date = now + timedelta(days=days_ahead)

        all_events: list[Event] = []
        page = 1
        per_page = 50

        async with httpx.AsyncClient(timeout=20.0) as client:
            while True:
                events_page, total_entries = await self._fetch_page(
                    client,
                    min_date=now.strftime("%Y-%m-%d"),
                    max_date=end_date.strftime("%Y-%m-%d"),
                    page=page,
                    per_page=per_page,
                )
                all_events.extend(events_page)

                # Stop if we've fetched everything or got an empty page
                if not events_page or len(all_events) >= total_entries:
                    break
                page += 1

        logger.info(
            f"Songkick: collected {len(all_events)} events in Madrid"
        )
        return all_events

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        min_date: str,
        max_date: str,
        page: int,
        per_page: int,
    ) -> tuple[list[Event], int]:
        """Fetch a single page of events from the Songkick calendar endpoint.

        Returns:
            Tuple of (parsed events, total_entries from API).
        """
        url = f"{BASE_URL}/metro_areas/{MADRID_METRO_AREA_ID}/calendar.json"

        try:
            response = await client.get(
                url,
                params={
                    "apikey": self.api_key,
                    "min_date": min_date,
                    "max_date": max_date,
                    "page": page,
                    "per_page": per_page,
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(f"Songkick request failed (page {page}): {exc}")
            return [], 0

        try:
            data = response.json()
        except Exception:
            logger.warning("Songkick returned invalid JSON")
            return [], 0

        results_page = data.get("resultsPage", {})
        total_entries = results_page.get("totalEntries", 0)
        raw_events = results_page.get("results", {}).get("event", [])

        if not raw_events:
            return [], total_entries

        events = []
        for raw in raw_events:
            event = self._parse_event(raw)
            if event is not None:
                events.append(event)

        return events, total_entries

    def _parse_event(self, raw: dict) -> Event | None:
        """Parse a single Songkick event dict into an Event model."""
        try:
            display_name = raw.get("displayName", "").strip()
            if not display_name:
                return None

            # Parse date — Songkick uses "start" with a "date" field
            start = raw.get("start", {})
            raw_date = start.get("datetime") or start.get("date")
            if not raw_date:
                return None

            event_date = datetime.fromisoformat(
                raw_date.replace("Z", "+00:00")
            )
            if event_date.tzinfo is None:
                event_date = event_date.replace(tzinfo=timezone.utc)

            # Build venue
            venue = None
            venue_data = raw.get("venue", {})
            if venue_data and venue_data.get("displayName"):
                venue = Venue(
                    name=venue_data["displayName"],
                    city="Madrid",
                    url=venue_data.get("uri"),
                    latitude=_safe_float(venue_data.get("lat")),
                    longitude=_safe_float(venue_data.get("lng")),
                )

            # Extract artist names from the performance array
            performances = raw.get("performance") or []
            artist_names = [
                p["displayName"]
                for p in performances
                if p.get("displayName")
            ]

            # Event URL
            url = raw.get("uri")

            return Event(
                name=display_name,
                artists=artist_names,
                venue=venue,
                date=event_date,
                url=url,
                source=EventSource.SONGKICK,
            )
        except Exception as exc:
            logger.warning(f"Failed to parse Songkick event: {exc}")
            return None


def _safe_float(value: object) -> float | None:
    """Convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
        return result if result != 0.0 else None
    except (TypeError, ValueError):
        return None
