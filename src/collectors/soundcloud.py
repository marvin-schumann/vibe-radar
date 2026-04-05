"""SoundCloud collector — scrapes liked tracks, reposts, and followings.

SoundCloud's official API is largely deprecated, so this module uses httpx
to hit their internal ``api-v2`` endpoints.  A ``client_id`` is extracted
from the JavaScript bundles served on the public site.
"""

from __future__ import annotations

import re
from typing import Any

import httpx
from bs4 import BeautifulSoup
from loguru import logger

from src.config import settings
from src.models import Artist, MusicSource

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASE_URL = "https://soundcloud.com"
_API_V2 = "https://api-v2.soundcloud.com"

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://soundcloud.com/",
}

_PAGE_LIMIT = 200  # max items per API page


class SoundCloudCollector:
    """Collect artists from a SoundCloud user's activity.

    Gathers artist names from:
    * liked tracks
    * reposts
    * followed users (artists the user explicitly follows)
    """

    def __init__(self, username: str | None = None) -> None:
        self.username: str = username or settings.soundcloud_username
        if not self.username:
            raise ValueError(
                "SoundCloud username must be provided or set via "
                "SOUNDCLOUD_USERNAME env var."
            )
        self._client_id: str | None = None
        self._user_id: int | None = None
        self.track_counts: dict[str, int] = {}  # normalized_name → liked track count
        self.liked_events: list[tuple[str, str]] = []  # (normalized_name, created_at ISO string)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def collect_artists(self) -> list[Artist]:
        """Return de-duplicated artists from likes, reposts, and followings."""
        seen: dict[str, Artist] = {}

        async with httpx.AsyncClient(
            headers=_BROWSER_HEADERS,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
        ) as client:
            # Step 1 — obtain a client_id
            try:
                self._client_id = await self._get_client_id(client)
            except Exception:
                logger.warning(
                    "Could not extract SoundCloud client_id — "
                    "falling back to HTML scraping only."
                )
                return await self._fallback_html_scrape(client)

            # Step 2 — resolve username → user_id
            try:
                self._user_id = await self._resolve_user_id(client)
            except Exception:
                logger.warning(
                    "Could not resolve SoundCloud user_id for '{}' — "
                    "falling back to HTML scraping.",
                    self.username,
                )
                return await self._fallback_html_scrape(client)

            # Step 3 — collect from liked tracks only.
            # Followings and reposts excluded: following ≠ listening, and reposts
            # are often DJ mixes / other people's content rather than artists the
            # user actively follows. Likes are the strongest signal on SoundCloud.
            for label, coro in [
                ("likes", self._fetch_liked_artists(client)),
            ]:
                try:
                    artists = await coro
                    for artist in artists:
                        key = artist.normalized_name
                        if key:
                            self.track_counts[key] = self.track_counts.get(key, 0) + 1
                            if key not in seen:
                                seen[key] = artist
                    logger.info(
                        "SoundCloud {}: found {} artists ({} new)",
                        label,
                        len(artists),
                        sum(1 for a in artists if a.normalized_name in seen),
                    )
                except Exception as exc:
                    logger.warning(
                        "SoundCloud {} collection failed: {}", label, exc
                    )

        result = list(seen.values())
        logger.info(
            "SoundCloud collector finished — {} unique artists", len(result)
        )
        return result

    # ------------------------------------------------------------------
    # Internal: client_id extraction
    # ------------------------------------------------------------------

    async def _get_client_id(self, client: httpx.AsyncClient) -> str:
        """Fetch the SoundCloud homepage and pull the client_id from JS bundles."""
        resp = await client.get(_BASE_URL)
        resp.raise_for_status()

        # SoundCloud embeds <script crossorigin src="https://a-v2.sndcdn.com/assets/0-xxx.js">
        # One of those bundles contains the client_id literal.
        soup = BeautifulSoup(resp.text, "html.parser")
        script_urls: list[str] = [
            tag["src"]
            for tag in soup.find_all("script", src=True)
            if "sndcdn.com" in tag["src"]
        ]

        for url in script_urls:
            try:
                js_resp = await client.get(url)
                js_resp.raise_for_status()
                match = re.search(r'client_id\s*[:=]\s*"([a-zA-Z0-9]{32})"', js_resp.text)
                if match:
                    client_id = match.group(1)
                    logger.debug("Extracted SoundCloud client_id: {}…", client_id[:8])
                    return client_id
            except Exception:
                continue

        raise RuntimeError("Failed to extract client_id from SoundCloud JS bundles")

    # ------------------------------------------------------------------
    # Internal: user resolution
    # ------------------------------------------------------------------

    async def _resolve_user_id(self, client: httpx.AsyncClient) -> int:
        """Resolve a SoundCloud username to a numeric user_id."""
        resp = await client.get(
            f"{_API_V2}/resolve",
            params={
                "url": f"{_BASE_URL}/{self.username}",
                "client_id": self._client_id,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        user_id: int = data["id"]
        logger.debug("Resolved '{}' → user_id {}", self.username, user_id)
        return user_id

    # ------------------------------------------------------------------
    # Internal: API-based fetching
    # ------------------------------------------------------------------

    async def _fetch_liked_artists(self, client: httpx.AsyncClient) -> list[Artist]:
        """Fetch artists from the user's liked tracks (capped at 400 most recent)."""
        items = await self._paginate(
            client,
            f"{_API_V2}/users/{self._user_id}/track_likes",
            max_pages=2,
        )
        artists: list[Artist] = []
        for item in items:
            track = item.get("track") or item
            artist = self._artist_from_track(track)
            if artist:
                artists.append(artist)
                # Capture the liked-at timestamp from the outer item
                created_at = item.get("created_at")
                if created_at and artist.normalized_name:
                    self.liked_events.append((artist.normalized_name, created_at))
        return artists

    async def _fetch_repost_artists(self, client: httpx.AsyncClient) -> list[Artist]:
        """Fetch artists from the user's reposts (capped at 200 most recent)."""
        items = await self._paginate(
            client,
            f"{_API_V2}/stream/users/{self._user_id}/reposts",
            max_pages=1,
        )
        artists: list[Artist] = []
        for item in items:
            track = item.get("track") or item
            artist = self._artist_from_track(track)
            if artist:
                artists.append(artist)
        return artists

    async def _fetch_following_artists(self, client: httpx.AsyncClient) -> list[Artist]:
        """Fetch artists the user follows."""
        items = await self._paginate(
            client,
            f"{_API_V2}/users/{self._user_id}/followings",
        )
        artists: list[Artist] = []
        for item in items:
            user = item.get("collection", item) if isinstance(item, dict) else item
            # followings endpoint may nest inside "collection" at the page
            # level (handled by _paginate) — each item is a user object.
            artist = self._artist_from_user(item)
            if artist:
                artists.append(artist)
        return artists

    # ------------------------------------------------------------------
    # Internal: pagination helper
    # ------------------------------------------------------------------

    async def _paginate(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        max_pages: int = 5,
    ) -> list[dict[str, Any]]:
        """Follow SoundCloud's cursor-based pagination, returning all items."""
        all_items: list[dict[str, Any]] = []
        params: dict[str, Any] = {
            "client_id": self._client_id,
            "limit": _PAGE_LIMIT,
        }
        next_href: str | None = url

        for _ in range(max_pages):
            if next_href is None:
                break
            try:
                resp = await client.get(next_href, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("Pagination request failed: {}", exc)
                break

            collection = data.get("collection", [])
            all_items.extend(collection)
            next_href = data.get("next_href")
            # After the first request, next_href already contains query
            # params including client_id, so clear our params.
            params = {}

        return all_items

    # ------------------------------------------------------------------
    # Internal: model builders
    # ------------------------------------------------------------------

    @staticmethod
    def _artist_from_track(track: dict[str, Any]) -> Artist | None:
        """Build an Artist from a SoundCloud track JSON object.

        Priority: publisher_metadata.artist > uploader username.
        publisher_metadata.artist is the official credited artist when
        available. Falls back to the uploader username, which for
        electronic music is usually the DJ/producer themselves.
        """
        user = track.get("user") or {}
        pm = track.get("publisher_metadata") or {}

        # Prefer the official publisher artist name
        name = pm.get("artist")
        image_url = user.get("avatar_url")
        source_url = user.get("permalink_url")

        if not name:
            # Fall back to uploader — in electronic music, uploaders
            # are usually the actual artist (DJs self-publish on SC)
            name = user.get("username") or user.get("full_name")

        if not name:
            return None

        # If publisher_metadata.artist has multiple artists (comma-separated),
        # take the first one as primary
        if "," in name:
            name = name.split(",")[0].strip()

        return Artist(
            name=name,
            source=MusicSource.SOUNDCLOUD,
            source_url=source_url,
            image_url=image_url,
            genres=[track["genre"]] if track.get("genre") else [],
            play_count=track.get("playback_count"),
        )

    @staticmethod
    def _artist_from_user(user: dict[str, Any]) -> Artist | None:
        """Build an Artist from a SoundCloud user JSON object."""
        name = user.get("username") or user.get("full_name")
        if not name:
            return None
        return Artist(
            name=name,
            source=MusicSource.SOUNDCLOUD,
            source_url=user.get("permalink_url"),
            image_url=user.get("avatar_url"),
        )

    # ------------------------------------------------------------------
    # Fallback: HTML scraping when API is unavailable
    # ------------------------------------------------------------------

    async def _fallback_html_scrape(self, client: httpx.AsyncClient) -> list[Artist]:
        """Scrape the public profile page when the internal API is blocked."""
        logger.info("Attempting HTML fallback scrape for '{}'", self.username)
        artists: list[Artist] = []

        for path in [
            f"/{self.username}",
            f"/{self.username}/likes",
            f"/{self.username}/following",
        ]:
            try:
                resp = await client.get(f"{_BASE_URL}{path}")
                resp.raise_for_status()
                artists.extend(self._parse_artists_from_page(resp.text))
            except Exception as exc:
                logger.warning("HTML scrape of {} failed: {}", path, exc)

        # De-duplicate
        seen: dict[str, Artist] = {}
        for artist in artists:
            key = artist.normalized_name
            if key and key not in seen:
                seen[key] = artist
        result = list(seen.values())
        logger.info("HTML fallback collected {} unique artists", len(result))
        return result

    @staticmethod
    def _parse_artists_from_page(html: str) -> list[Artist]:
        """Extract artist information from a SoundCloud HTML page.

        SoundCloud heavily relies on client-side rendering, so the HTML
        contains limited data.  We look for:
        * ``<noscript>`` content with user links
        * Embedded JSON (hydration data) in ``<script>`` tags
        * ``<a>`` tags pointing to user profiles
        """
        soup = BeautifulSoup(html, "html.parser")
        artists: list[Artist] = []

        # --- Strategy 1: embedded hydration JSON ---
        for script_tag in soup.find_all("script"):
            text = script_tag.string or ""
            if "hydratable" not in text and "__sc_hydration" not in text:
                continue
            # Try to extract user objects from the hydration data.
            for match in re.finditer(
                r'"username"\s*:\s*"([^"]+)".*?"permalink_url"\s*:\s*"([^"]*)"',
                text,
            ):
                name, url = match.group(1), match.group(2)
                if name:
                    artists.append(
                        Artist(
                            name=name,
                            source=MusicSource.SOUNDCLOUD,
                            source_url=url or None,
                        )
                    )

        # --- Strategy 2: <a> tags with user profile hrefs ---
        for link in soup.find_all("a", href=True):
            href: str = link["href"]
            # User profile links look like "/username" (single path segment).
            if (
                href.startswith("/")
                and href.count("/") == 1
                and len(href) > 1
                and not href.startswith("/#")
            ):
                name = link.get_text(strip=True)
                if name and len(name) < 100:
                    artists.append(
                        Artist(
                            name=name,
                            source=MusicSource.SOUNDCLOUD,
                            source_url=f"{_BASE_URL}{href}",
                        )
                    )

        # --- Strategy 3: noscript content ---
        for noscript in soup.find_all("noscript"):
            inner = BeautifulSoup(noscript.decode_contents(), "html.parser")
            for link in inner.find_all("a", href=True):
                href = link["href"]
                if "soundcloud.com/" in href:
                    name = link.get_text(strip=True)
                    if name and len(name) < 100:
                        artists.append(
                            Artist(
                                name=name,
                                source=MusicSource.SOUNDCLOUD,
                                source_url=href,
                            )
                        )

        return artists
