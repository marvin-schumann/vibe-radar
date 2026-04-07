"""DJ profile collector — fetches genre distributions from SoundCloud.

Batch job: iterates over DJ profiles from ``src/data/dj_profiles.json``,
resolves each DJ's SoundCloud user, fetches their liked tracks, and
builds a genre distribution (taste vector) for each DJ.

Results are cached to ``src/data/dj_taste_vectors.json`` so the matching
engine can load them without re-scraping.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
from loguru import logger

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).parent.parent / "data"
_PROFILES_PATH = _DATA_DIR / "dj_profiles.json"
_VECTORS_PATH = _DATA_DIR / "dj_taste_vectors.json"

# ---------------------------------------------------------------------------
# SoundCloud constants (shared with soundcloud.py collector)
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

_PAGE_LIMIT = 200
_RATE_LIMIT_DELAY = 1.0  # seconds between requests


# ---------------------------------------------------------------------------
# DJProfileCollector
# ---------------------------------------------------------------------------

class DJProfileCollector:
    """Scrape SoundCloud liked tracks for a list of DJs and build taste vectors.

    Usage::

        collector = DJProfileCollector()
        vectors = await collector.collect(limit=20)  # scrape first 20 DJs
    """

    def __init__(self) -> None:
        self._client_id: str | None = None
        self._existing_vectors: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def collect(self, *, limit: int | None = None) -> dict[str, dict]:
        """Collect taste vectors for DJs, merging with any already-cached results.

        Args:
            limit: Max number of DJs to scrape in this run (None = all).

        Returns:
            Full dict of DJ name → taste vector (including previously cached).
        """
        profiles = self._load_profiles()
        self._existing_vectors = self._load_cached_vectors()

        # Skip DJs we already have vectors for
        to_scrape = [
            p for p in profiles
            if p["name"] not in self._existing_vectors
        ]
        if limit is not None:
            to_scrape = to_scrape[:limit]

        logger.info(
            "DJ profile collector: {} total profiles, {} cached, {} to scrape",
            len(profiles),
            len(self._existing_vectors),
            len(to_scrape),
        )

        if not to_scrape:
            logger.info("All DJ profiles already cached — nothing to scrape")
            return self._existing_vectors

        async with httpx.AsyncClient(
            headers=_BROWSER_HEADERS,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
        ) as client:
            # Step 1: get client_id
            self._client_id = await self._get_client_id(client)

            # Step 2: scrape each DJ sequentially (rate limited)
            for i, profile in enumerate(to_scrape, 1):
                name = profile["name"]
                sc_username = profile["soundcloud"]
                logger.info(
                    "[{}/{}] Scraping {} (soundcloud.com/{})",
                    i, len(to_scrape), name, sc_username,
                )
                try:
                    vector = await self._scrape_dj(client, sc_username, profile)
                    self._existing_vectors[name] = vector
                    logger.info(
                        "  → {} genres, top: {}",
                        len(vector["genre_distribution"]),
                        list(vector["genre_distribution"].items())[:3],
                    )
                except Exception as exc:
                    logger.warning("  → Failed to scrape {}: {}", name, exc)

                # Rate limit
                if i < len(to_scrape):
                    await asyncio.sleep(_RATE_LIMIT_DELAY)

        # Save merged results
        self._save_vectors(self._existing_vectors)
        logger.info(
            "DJ taste vectors saved: {} total ({} new this run)",
            len(self._existing_vectors),
            len(to_scrape),
        )
        return self._existing_vectors

    # ------------------------------------------------------------------
    # Internal: SoundCloud API helpers
    # ------------------------------------------------------------------

    async def _get_client_id(self, client: httpx.AsyncClient) -> str:
        """Extract SoundCloud client_id from JS bundles."""
        resp = await client.get(_BASE_URL)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        script_urls = [
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
                    cid = match.group(1)
                    logger.debug("Extracted SoundCloud client_id: {}…", cid[:8])
                    return cid
            except Exception:
                continue

        raise RuntimeError("Failed to extract client_id from SoundCloud JS bundles")

    async def _resolve_user_id(
        self, client: httpx.AsyncClient, username: str
    ) -> int:
        """Resolve a SoundCloud username to a numeric user_id."""
        resp = await client.get(
            f"{_API_V2}/resolve",
            params={
                "url": f"{_BASE_URL}/{username}",
                "client_id": self._client_id,
            },
        )
        resp.raise_for_status()
        return resp.json()["id"]

    async def _fetch_liked_tracks(
        self, client: httpx.AsyncClient, user_id: int, *, max_pages: int = 2
    ) -> list[dict]:
        """Fetch liked tracks for a user (max ~400 tracks)."""
        all_items: list[dict] = []
        params: dict[str, Any] = {
            "client_id": self._client_id,
            "limit": _PAGE_LIMIT,
        }
        next_href: str | None = f"{_API_V2}/users/{user_id}/track_likes"

        for _ in range(max_pages):
            if next_href is None:
                break
            try:
                resp = await client.get(next_href, params=params)
                resp.raise_for_status()
                data = resp.json()
            except Exception as exc:
                logger.warning("Pagination failed: {}", exc)
                break

            collection = data.get("collection", [])
            all_items.extend(collection)
            next_href = data.get("next_href")
            params = {}  # next_href includes query params

            # Rate limit between pages
            if next_href:
                await asyncio.sleep(_RATE_LIMIT_DELAY)

        return all_items

    # ------------------------------------------------------------------
    # Internal: DJ scraping + vector building
    # ------------------------------------------------------------------

    async def _scrape_dj(
        self,
        client: httpx.AsyncClient,
        username: str,
        profile: dict,
    ) -> dict:
        """Scrape a single DJ and return their taste vector."""
        user_id = await self._resolve_user_id(client, username)
        await asyncio.sleep(_RATE_LIMIT_DELAY)

        liked_items = await self._fetch_liked_tracks(client, user_id)

        # Extract genres from liked tracks
        genre_counter: Counter[str] = Counter()
        for item in liked_items:
            track = item.get("track") or item
            genre = track.get("genre")
            if genre and genre.strip():
                genre_counter[genre.lower().strip()] += 1

        # Normalize to percentages
        total = sum(genre_counter.values()) or 1
        genre_distribution = {
            genre: round(count / total * 100, 1)
            for genre, count in genre_counter.most_common()
        }

        return {
            "name": profile["name"],
            "soundcloud": username,
            "soundcloud_url": f"{_BASE_URL}/{username}",
            "city": profile.get("city", ""),
            "curated_genres": profile.get("genres", []),
            "genre_distribution": genre_distribution,
            "total_liked_tracks": len(liked_items),
        }

    # ------------------------------------------------------------------
    # File I/O
    # ------------------------------------------------------------------

    @staticmethod
    def _load_profiles() -> list[dict]:
        """Load DJ profiles from the curated JSON file."""
        if not _PROFILES_PATH.exists():
            raise FileNotFoundError(f"DJ profiles not found at {_PROFILES_PATH}")
        with open(_PROFILES_PATH) as f:
            return json.load(f)

    @staticmethod
    def _load_cached_vectors() -> dict[str, dict]:
        """Load previously cached taste vectors, or return empty dict."""
        if not _VECTORS_PATH.exists():
            return {}
        try:
            with open(_VECTORS_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def _save_vectors(vectors: dict[str, dict]) -> None:
        """Persist taste vectors to JSON."""
        _VECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_VECTORS_PATH, "w") as f:
            json.dump(vectors, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CLI entry point for batch scraping
# ---------------------------------------------------------------------------

async def main() -> None:
    """Run the DJ profile collector as a standalone script."""
    import argparse

    parser = argparse.ArgumentParser(description="Scrape DJ profiles from SoundCloud")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Max DJs to scrape (default: all uncached)",
    )
    args = parser.parse_args()

    collector = DJProfileCollector()
    vectors = await collector.collect(limit=args.limit)

    print(f"\nTotal DJ vectors: {len(vectors)}")
    for name, v in list(vectors.items())[:5]:
        top = list(v["genre_distribution"].items())[:3]
        print(f"  {name}: {top}")


if __name__ == "__main__":
    asyncio.run(main())
