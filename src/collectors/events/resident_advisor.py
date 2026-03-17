"""Resident Advisor event collector using their internal GraphQL API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx
from loguru import logger

from src.config import settings
from src.models import Event, EventSource, Venue

GRAPHQL_URL = "https://ra.co/graphql"

EVENTS_QUERY = """
query GET_EVENTS($filters: FilterInputDtoInput, $pageSize: Int) {
  eventListings(filters: $filters, pageSize: $pageSize) {
    data {
      event {
        title
        date
        contentUrl
        images {
          filename
        }
        venue {
          name
          address
        }
        artists {
          name
        }
      }
    }
  }
}
"""

RA_MADRID_AREA_ID = 49


class ResidentAdvisorCollector:
    """Collects electronic music events from Resident Advisor."""

    async def collect_events(self, days_ahead: int = 30) -> list[Event]:
        """Fetch upcoming events in Madrid from Resident Advisor.

        Args:
            days_ahead: Number of days into the future to search.

        Returns:
            List of parsed Event models. Empty list on failure.
        """
        now = datetime.now(tz=timezone.utc)
        end_date = now + timedelta(days=days_ahead)

        variables = {
            "filters": {
                "areas": {"eq": RA_MADRID_AREA_ID},
                "listingDate": {
                    "gte": now.strftime("%Y-%m-%dT00:00:00.000Z"),
                    "lte": end_date.strftime("%Y-%m-%dT00:00:00.000Z"),
                },
                "listingType": {"eq": "CLUB"},
            },
            "pageSize": 100,
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    GRAPHQL_URL,
                    json={"query": EVENTS_QUERY, "variables": variables},
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://ra.co/events",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(f"Resident Advisor request failed: {exc}")
            return []

        try:
            data = response.json()
        except Exception:
            logger.warning("Resident Advisor returned invalid JSON")
            return []

        return self._parse_events(data)

    def _parse_events(self, data: dict) -> list[Event]:
        """Parse the GraphQL response into Event models."""
        events: list[Event] = []

        listings = (
            data.get("data", {}).get("eventListings", {}).get("data", [])
        )
        if not listings:
            logger.info("No event listings returned from Resident Advisor")
            return events

        for listing in listings:
            try:
                event_data = listing.get("event", {})
                if not event_data:
                    continue

                title = event_data.get("title", "").strip()
                if not title:
                    continue

                # Parse date
                raw_date = event_data.get("date")
                if not raw_date:
                    continue
                event_date = datetime.fromisoformat(
                    raw_date.replace("Z", "+00:00")
                )

                # Build venue
                venue = None
                venue_data = event_data.get("venue")
                if venue_data:
                    venue = Venue(
                        name=venue_data.get("name", "Unknown Venue"),
                        address=venue_data.get("address"),
                        url=None,
                    )

                # Extract artist names
                artists_data = event_data.get("artists") or []
                artist_names = [
                    a["name"]
                    for a in artists_data
                    if a.get("name")
                ]

                # Build event URL
                content_url = event_data.get("contentUrl", "")
                url = f"https://ra.co{content_url}" if content_url else None

                # Image
                images = event_data.get("images") or []
                image_url = None
                if images:
                    filename = images[0].get("filename")
                    if filename:
                        image_url = f"https://ra.co/images/events/flyer/{filename}"

                events.append(
                    Event(
                        name=title,
                        artists=artist_names,
                        venue=venue,
                        date=event_date,
                        url=url,
                        image_url=image_url,
                        source=EventSource.RESIDENT_ADVISOR,
                    )
                )
            except Exception as exc:
                logger.warning(
                    f"Failed to parse RA event listing: {exc}"
                )
                continue

        logger.info(
            f"Resident Advisor: collected {len(events)} events in Madrid"
        )
        return events
