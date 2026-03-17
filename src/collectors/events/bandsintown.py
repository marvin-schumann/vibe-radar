"""Bandsintown event collector using their REST API."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import httpx
from loguru import logger

from src.config import settings
from src.models import Event, EventSource, Venue

BASE_URL = "https://rest.bandsintown.com"


class BandsintownCollector:
    """Collects music events from Bandsintown.

    Supports two strategies:
      1. Artist-based: check if specific artists have Madrid shows.
      2. General: (future) venue-based discovery for Madrid events.
    """

    def __init__(self) -> None:
        self.app_id = settings.bandsintown_app_id

    async def collect_events(
        self,
        artist_names: list[str] | None = None,
        days_ahead: int = 30,
    ) -> list[Event]:
        """Fetch upcoming events in Madrid from Bandsintown.

        Args:
            artist_names: Optional list of artists to check for Madrid events.
                          If None, returns an empty list (venue search not yet
                          supported by the public API).
            days_ahead: Number of days into the future to search.

        Returns:
            List of parsed Event models. Empty list on failure.
        """
        if not self.app_id:
            logger.warning(
                "Bandsintown app_id not configured — skipping collection. "
                "Set BANDSINTOWN_APP_ID in your .env file."
            )
            return []

        if not artist_names:
            logger.info(
                "No artist names provided for Bandsintown lookup; "
                "skipping collection"
            )
            return []

        cutoff = datetime.now(tz=timezone.utc) + timedelta(days=days_ahead)
        events: list[Event] = []

        async with httpx.AsyncClient(timeout=20.0) as client:
            for artist_name in artist_names:
                artist_events = await self._fetch_artist_events(
                    client, artist_name, cutoff
                )
                events.extend(artist_events)

        logger.info(
            f"Bandsintown: collected {len(events)} Madrid events "
            f"across {len(artist_names)} artist lookups"
        )
        return events

    async def _fetch_artist_events(
        self,
        client: httpx.AsyncClient,
        artist_name: str,
        cutoff: datetime,
    ) -> list[Event]:
        """Fetch events for a single artist and filter to Madrid."""
        encoded_name = quote(artist_name, safe="")
        url = f"{BASE_URL}/artists/{encoded_name}/events"

        try:
            response = await client.get(
                url,
                params={"app_id": self.app_id, "date": "upcoming"},
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(
                f"Bandsintown request failed for '{artist_name}': {exc}"
            )
            return []

        try:
            data = response.json()
        except Exception:
            logger.warning(
                f"Bandsintown returned invalid JSON for '{artist_name}'"
            )
            return []

        if not isinstance(data, list):
            # The API sometimes returns an error object instead of a list
            return []

        events: list[Event] = []
        for raw in data:
            event = self._parse_event(raw, artist_name, cutoff)
            if event is not None:
                events.append(event)

        return events

    def _parse_event(
        self,
        raw: dict,
        queried_artist: str,
        cutoff: datetime,
    ) -> Event | None:
        """Parse a single Bandsintown event dict, returning None if not in Madrid or out of range."""
        try:
            # Filter to Madrid (case-insensitive city match)
            venue_data = raw.get("venue", {})
            city = (venue_data.get("city") or "").strip()
            country = (venue_data.get("country") or "").strip()

            if city.lower() != "madrid" or country.lower() not in (
                "spain",
                "es",
                "espana",
                "españa",
            ):
                return None

            # Parse date — Bandsintown returns "YYYY-MM-DDTHH:MM:SS"
            raw_date = raw.get("datetime", "")
            if not raw_date:
                return None
            event_date = datetime.fromisoformat(
                raw_date.replace("Z", "+00:00")
            )
            if event_date.tzinfo is None:
                event_date = event_date.replace(tzinfo=timezone.utc)
            if event_date > cutoff:
                return None

            # Build venue
            venue = Venue(
                name=venue_data.get("name", "Unknown Venue"),
                city="Madrid",
                address=venue_data.get("street_address"),
                latitude=_safe_float(venue_data.get("latitude")),
                longitude=_safe_float(venue_data.get("longitude")),
            )

            # Extract all artists on the lineup
            lineup_raw = raw.get("lineup") or []
            artist_names: list[str] = []
            if isinstance(lineup_raw, list):
                artist_names = [
                    name for name in lineup_raw if isinstance(name, str)
                ]
            if not artist_names:
                artist_names = [queried_artist]

            title = raw.get("title") or " + ".join(artist_names)

            return Event(
                name=title,
                artists=artist_names,
                venue=venue,
                date=event_date,
                url=raw.get("url"),
                image_url=raw.get("artist", {}).get("thumb_url"),
                source=EventSource.BANDSINTOWN,
                description=raw.get("description"),
            )
        except Exception as exc:
            logger.warning(
                f"Failed to parse Bandsintown event for "
                f"'{queried_artist}': {exc}"
            )
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
