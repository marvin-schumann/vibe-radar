"""Scrape SoundCloud profiles for ALL event artists across ALL cities.

Steps:
1. Read Madrid events, scrape RA events for Berlin, Amsterdam, London, Barcelona
2. Extract unique artist names from all cities
3. Search SoundCloud for each artist → add to dj_profiles.json
4. Scrape taste vectors for newly found artists → dj_taste_vectors.json
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup
from loguru import logger

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
SRC_DATA_DIR = ROOT / "src" / "data"
PROFILES_PATH = SRC_DATA_DIR / "dj_profiles.json"
VECTORS_PATH = SRC_DATA_DIR / "dj_taste_vectors.json"

# ---------------------------------------------------------------------------
# RA City config
# ---------------------------------------------------------------------------

RA_CITIES = {
    "berlin": 34,
    "amsterdam": 29,
    "london": 13,
    "barcelona": 44,
}

RA_GRAPHQL_URL = "https://ra.co/graphql"

RA_EVENTS_QUERY = """
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

# ---------------------------------------------------------------------------
# SoundCloud constants
# ---------------------------------------------------------------------------

SC_BASE_URL = "https://soundcloud.com"
SC_API_V2 = "https://api-v2.soundcloud.com"

BROWSER_HEADERS = {
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

RATE_LIMIT = 2.0  # seconds between requests
SAVE_EVERY = 20
PAGE_LIMIT = 200
MIN_FOLLOWERS = 500  # minimum followers to accept a SC search result


# ---------------------------------------------------------------------------
# Helper: slugify artist name for SC URL resolution
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    """Convert artist name to SoundCloud-style slug."""
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    name = name.lower().strip()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    name = re.sub(r"-+", "-", name)
    return name.strip("-")


def parse_artists_from_title(title: str) -> list[str]:
    """Extract artist names from an RA event title."""
    if ":" not in title:
        return []
    lineup_part = title.split(":", 1)[1].strip()
    lineup_part = re.sub(r"\([^)]*FREE TICKETS[^)]*\)", "", lineup_part, flags=re.IGNORECASE)
    lineup_part = re.sub(r"\([^)]*ONLY IN RA[^)]*\)", "", lineup_part, flags=re.IGNORECASE)
    lineup_part = re.sub(r"\([^)]*\d+H SET[^)]*\)", "", lineup_part, flags=re.IGNORECASE)
    parts = re.split(r"[,/]|\s\+\s", lineup_part)
    artists = []
    for part in parts:
        b2b_parts = re.split(r"\s+[Bb]2[Bb]\s+", part.strip())
        for bp in b2b_parts:
            name = bp.strip()
            if name and len(name) >= 2 and not re.match(
                r"^(feat\.?|w/|presents?|invites?)$", name, re.IGNORECASE
            ):
                artists.append(name)
    return artists


# ---------------------------------------------------------------------------
# Step 1: Scrape RA events for other cities
# ---------------------------------------------------------------------------

async def scrape_ra_events(city: str, area_id: int, days_ahead: int = 30) -> list[dict]:
    """Fetch upcoming events from Resident Advisor for a city."""
    now = datetime.now(tz=timezone.utc)
    end_date = now + timedelta(days=days_ahead)

    variables = {
        "filters": {
            "areas": {"eq": area_id},
            "listingDate": {
                "gte": now.strftime("%Y-%m-%dT00:00:00.000Z"),
                "lte": end_date.strftime("%Y-%m-%dT00:00:00.000Z"),
            },
            "listingType": {"eq": "CLUB"},
        },
        "pageSize": 100,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            response = await client.post(
                RA_GRAPHQL_URL,
                json={"query": RA_EVENTS_QUERY, "variables": variables},
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://ra.co/events",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning(f"RA request failed for {city}: {exc}")
            return []

        try:
            data = response.json()
        except Exception:
            logger.warning(f"RA returned invalid JSON for {city}")
            return []

    listings = data.get("data", {}).get("eventListings", {}).get("data", [])
    if not listings:
        logger.info(f"No event listings from RA for {city}")
        return []

    events = []
    for listing in listings:
        try:
            event_data = listing.get("event", {})
            if not event_data:
                continue

            title = event_data.get("title", "").strip()
            if not title:
                continue

            raw_date = event_data.get("date")
            if not raw_date:
                continue
            event_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))

            venue_data = event_data.get("venue") or {}
            venue = {
                "name": venue_data.get("name", "Unknown Venue"),
                "city": city.title(),
                "address": venue_data.get("address"),
                "url": None,
                "latitude": None,
                "longitude": None,
            }

            # Extract artist names
            artists_data = event_data.get("artists") or []
            artist_names = [a["name"] for a in artists_data if a.get("name")]

            title_artists = parse_artists_from_title(title)
            api_names_lower = {n.lower().strip() for n in artist_names}
            for ta in title_artists:
                if ta.lower().strip() not in api_names_lower:
                    artist_names.append(ta)

            content_url = event_data.get("contentUrl", "")
            url = f"https://ra.co{content_url}" if content_url else None

            images = event_data.get("images") or []
            image_url = None
            if images:
                filename = images[0].get("filename")
                if filename:
                    image_url = f"https://ra.co/images/events/flyer/{filename}"

            normalized_artists = [a.lower().strip() for a in artist_names]

            events.append({
                "name": title,
                "artists": artist_names,
                "normalized_artists": normalized_artists,
                "venue": venue,
                "date": event_date.isoformat(),
                "end_date": None,
                "url": url,
                "image_url": image_url,
                "source": "resident_advisor",
                "genres": [],
                "description": None,
                "price": None,
            })
        except Exception as exc:
            logger.warning(f"Failed to parse RA event for {city}: {exc}")
            continue

    logger.info(f"RA {city}: collected {len(events)} events")
    return events


# ---------------------------------------------------------------------------
# Step 2: Extract all unique artist names
# ---------------------------------------------------------------------------

def extract_artists_from_events(events_file: Path) -> set[str]:
    """Extract unique artist names from an events JSON file."""
    if not events_file.exists():
        return set()
    with open(events_file) as f:
        data = json.load(f)

    # Handle both formats: list of events or {events: [...]}
    if isinstance(data, dict):
        events = data.get("events", [])
    else:
        events = data

    artists = set()
    for event in events:
        for a in event.get("artists", []):
            name = a.strip()
            if name and len(name) >= 2:
                artists.add(name)
    return artists


# ---------------------------------------------------------------------------
# Step 3: Find SoundCloud profiles
# ---------------------------------------------------------------------------

async def get_sc_client_id(client: httpx.AsyncClient) -> str:
    """Extract SoundCloud client_id from JS bundles."""
    resp = await client.get(SC_BASE_URL)
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
                logger.debug(f"Extracted SC client_id: {cid[:8]}…")
                return cid
        except Exception:
            continue
    raise RuntimeError("Failed to extract client_id from SoundCloud JS bundles")


async def search_sc_user(
    client: httpx.AsyncClient,
    client_id: str,
    artist_name: str,
) -> dict | None:
    """Search SoundCloud for an artist, return user info if found."""
    slug = slugify(artist_name)

    # Strategy 1: try direct resolve
    try:
        resp = await client.get(
            f"{SC_API_V2}/resolve",
            params={
                "url": f"{SC_BASE_URL}/{slug}",
                "client_id": client_id,
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("kind") == "user" and data.get("followers_count", 0) >= MIN_FOLLOWERS:
                return {
                    "username": data["permalink"],
                    "display_name": data.get("full_name") or data.get("username", ""),
                    "followers": data.get("followers_count", 0),
                    "city": data.get("city", ""),
                    "country": data.get("country_code", ""),
                }
    except Exception:
        pass

    await asyncio.sleep(RATE_LIMIT)

    # Strategy 2: search endpoint
    try:
        resp = await client.get(
            f"{SC_API_V2}/search/users",
            params={
                "q": artist_name,
                "client_id": client_id,
                "limit": 5,
            },
        )
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("collection", [])
            for user in results:
                followers = user.get("followers_count", 0)
                if followers >= MIN_FOLLOWERS:
                    # Check name similarity
                    sc_name = (user.get("full_name") or user.get("username", "")).lower()
                    if (
                        artist_name.lower() in sc_name
                        or sc_name in artist_name.lower()
                        or slug == user.get("permalink", "").lower()
                    ):
                        return {
                            "username": user["permalink"],
                            "display_name": user.get("full_name") or user.get("username", ""),
                            "followers": followers,
                            "city": user.get("city", ""),
                            "country": user.get("country_code", ""),
                        }
    except Exception:
        pass

    return None


async def fetch_liked_tracks(
    client: httpx.AsyncClient,
    client_id: str,
    user_id: int,
    max_pages: int = 2,
) -> list[dict]:
    """Fetch liked tracks for a user."""
    all_items: list[dict] = []
    params: dict[str, Any] = {
        "client_id": client_id,
        "limit": PAGE_LIMIT,
    }
    next_href: str | None = f"{SC_API_V2}/users/{user_id}/track_likes"

    for _ in range(max_pages):
        if next_href is None:
            break
        try:
            resp = await client.get(next_href, params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.warning(f"Pagination failed: {exc}")
            break

        collection = data.get("collection", [])
        all_items.extend(collection)
        next_href = data.get("next_href")
        params = {}

        if next_href:
            await asyncio.sleep(RATE_LIMIT)

    return all_items


async def resolve_user_id(
    client: httpx.AsyncClient,
    client_id: str,
    username: str,
) -> int | None:
    """Resolve SC username to numeric user_id."""
    try:
        resp = await client.get(
            f"{SC_API_V2}/resolve",
            params={
                "url": f"{SC_BASE_URL}/{username}",
                "client_id": client_id,
            },
        )
        resp.raise_for_status()
        return resp.json()["id"]
    except Exception as exc:
        logger.warning(f"Failed to resolve user_id for {username}: {exc}")
        return None


def build_taste_vector(liked_items: list[dict], profile: dict) -> dict:
    """Build a taste vector from liked tracks."""
    genre_counter: Counter[str] = Counter()
    for item in liked_items:
        track = item.get("track") or item
        genre = track.get("genre")
        if genre and genre.strip():
            genre_counter[genre.lower().strip()] += 1

    total = sum(genre_counter.values()) or 1
    genre_distribution = {
        genre: round(count / total * 100, 1)
        for genre, count in genre_counter.most_common()
    }

    return {
        "name": profile["name"],
        "soundcloud": profile["soundcloud"],
        "soundcloud_url": f"{SC_BASE_URL}/{profile['soundcloud']}",
        "city": profile.get("city", ""),
        "curated_genres": profile.get("genres", []),
        "genre_distribution": genre_distribution,
        "total_liked_tracks": len(liked_items),
    }


# ---------------------------------------------------------------------------
# File I/O helpers
# ---------------------------------------------------------------------------

def load_profiles() -> list[dict]:
    if not PROFILES_PATH.exists():
        return []
    with open(PROFILES_PATH) as f:
        return json.load(f)


def save_profiles(profiles: list[dict]) -> None:
    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(PROFILES_PATH, "w") as f:
        json.dump(profiles, f, indent=2, ensure_ascii=False)


def load_vectors() -> dict[str, dict]:
    if not VECTORS_PATH.exists():
        return {}
    try:
        with open(VECTORS_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_vectors(vectors: dict[str, dict]) -> None:
    VECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(VECTORS_PATH, "w") as f:
        json.dump(vectors, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    logger.remove()
    logger.add(sys.stderr, level="INFO")

    # -----------------------------------------------------------------------
    # Step 1: Read existing Madrid events
    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("STEP 1: Extract artists from Madrid events")
    logger.info("=" * 60)

    madrid_artists = extract_artists_from_events(DATA_DIR / "madrid_events.json")
    logger.info(f"Madrid: {len(madrid_artists)} unique artists from events")

    # -----------------------------------------------------------------------
    # Step 2: Scrape RA events for other cities
    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("STEP 2: Scrape RA events for Berlin, Amsterdam, London, Barcelona")
    logger.info("=" * 60)

    all_city_artists: dict[str, set[str]] = {"madrid": madrid_artists}

    for city, area_id in RA_CITIES.items():
        events_file = DATA_DIR / f"{city}_events.json"

        # Always scrape fresh to get current lineups
        logger.info(f"Scraping RA events for {city} (area_id={area_id})...")
        events = await scrape_ra_events(city, area_id)

        if events:
            output = {
                "collected_at": datetime.now(tz=timezone.utc).isoformat(),
                "events": events,
            }
            with open(events_file, "w") as f:
                json.dump(output, f, indent=2, ensure_ascii=False, default=str)
            logger.info(f"Saved {len(events)} events to {events_file.name}")

        city_artists = extract_artists_from_events(events_file)
        all_city_artists[city] = city_artists
        logger.info(f"{city.title()}: {len(city_artists)} unique artists")

        # Rate limit between city requests
        await asyncio.sleep(RATE_LIMIT)

    # Combine all artists
    all_artists: set[str] = set()
    for city, artists in all_city_artists.items():
        all_artists.update(artists)

    logger.info(f"\nTotal unique artists across all cities: {len(all_artists)}")
    for city, artists in all_city_artists.items():
        logger.info(f"  {city.title()}: {len(artists)}")

    # -----------------------------------------------------------------------
    # Step 3: Filter out artists we already have profiles for
    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("STEP 3: Find SoundCloud profiles for event artists")
    logger.info("=" * 60)

    existing_profiles = load_profiles()
    existing_names_lower = {p["name"].lower() for p in existing_profiles}
    existing_sc_usernames = {p["soundcloud"].lower() for p in existing_profiles}

    # Build city mapping for new artists
    artist_city_map: dict[str, str] = {}
    for city, artists in all_city_artists.items():
        for a in artists:
            if a.lower() not in artist_city_map:
                artist_city_map[a.lower()] = city.title()

    new_artists = sorted(
        [a for a in all_artists if a.lower() not in existing_names_lower],
        key=str.lower,
    )
    logger.info(
        f"Already have profiles for {len(all_artists) - len(new_artists)} event artists, "
        f"{len(new_artists)} new to search"
    )

    if not new_artists:
        logger.info("No new artists to search for — done!")
        return

    # -----------------------------------------------------------------------
    # Step 3b: Search SoundCloud for each new artist
    # -----------------------------------------------------------------------
    found_count = 0
    not_found_count = 0
    rate_limited = False

    async with httpx.AsyncClient(
        headers=BROWSER_HEADERS,
        follow_redirects=True,
        timeout=httpx.Timeout(30.0),
    ) as client:
        # Get client_id
        logger.info("Extracting SoundCloud client_id...")
        try:
            client_id = await get_sc_client_id(client)
        except RuntimeError as exc:
            logger.error(f"Failed to get SC client_id: {exc}")
            return

        logger.info(f"Got client_id, searching for {len(new_artists)} artists...")

        for i, artist_name in enumerate(new_artists, 1):
            logger.info(f"[{i}/{len(new_artists)}] Searching SC for: {artist_name}")

            try:
                result = await search_sc_user(client, client_id, artist_name)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning(f"Rate limited after {found_count} found — stopping")
                    rate_limited = True
                    break
                logger.warning(f"  HTTP error for {artist_name}: {exc}")
                not_found_count += 1
                await asyncio.sleep(RATE_LIMIT)
                continue
            except Exception as exc:
                logger.warning(f"  Error searching for {artist_name}: {exc}")
                not_found_count += 1
                await asyncio.sleep(RATE_LIMIT)
                continue

            if result and result["username"].lower() not in existing_sc_usernames:
                city = artist_city_map.get(artist_name.lower(), "")
                new_profile = {
                    "name": artist_name,
                    "soundcloud": result["username"],
                    "genres": [],
                    "city": city or result.get("city", ""),
                }
                existing_profiles.append(new_profile)
                existing_sc_usernames.add(result["username"].lower())
                existing_names_lower.add(artist_name.lower())
                found_count += 1
                logger.info(
                    f"  → Found: soundcloud.com/{result['username']} "
                    f"({result['followers']} followers)"
                )
            else:
                not_found_count += 1
                if result:
                    logger.info(f"  → Already have SC user: {result['username']}")
                else:
                    logger.info(f"  → Not found on SoundCloud")

            # Save progress
            if i % SAVE_EVERY == 0:
                save_profiles(existing_profiles)
                logger.info(f"  Progress saved: {len(existing_profiles)} total profiles")

            await asyncio.sleep(RATE_LIMIT)

        # Final save of profiles
        save_profiles(existing_profiles)
        logger.info(
            f"\nSC search complete: Found {found_count}/{len(new_artists)} event artists, "
            f"{not_found_count} not found"
        )

        if rate_limited:
            logger.warning("Stopped early due to rate limiting — will scrape vectors for what we have")

        # -------------------------------------------------------------------
        # Step 4: Scrape taste vectors for newly added artists
        # -------------------------------------------------------------------
        logger.info("=" * 60)
        logger.info("STEP 4: Scrape taste vectors for new artists")
        logger.info("=" * 60)

        existing_vectors = load_vectors()
        profiles_needing_vectors = [
            p for p in existing_profiles
            if p["name"] not in existing_vectors and p["soundcloud"]
        ]

        logger.info(
            f"Need vectors for {len(profiles_needing_vectors)} profiles "
            f"({len(existing_vectors)} already cached)"
        )

        vectors_scraped = 0
        vectors_failed = 0

        for i, profile in enumerate(profiles_needing_vectors, 1):
            name = profile["name"]
            username = profile["soundcloud"]
            logger.info(f"[{i}/{len(profiles_needing_vectors)}] Scraping vectors for {name} ({username})")

            try:
                user_id = await resolve_user_id(client, client_id, username)
                if user_id is None:
                    vectors_failed += 1
                    continue

                await asyncio.sleep(RATE_LIMIT)

                liked_items = await fetch_liked_tracks(client, client_id, user_id)
                vector = build_taste_vector(liked_items, profile)
                existing_vectors[name] = vector
                vectors_scraped += 1
                logger.info(
                    f"  → {len(vector['genre_distribution'])} genres, "
                    f"{vector['total_liked_tracks']} liked tracks"
                )
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    logger.warning(f"Rate limited during vector scraping after {vectors_scraped} — stopping")
                    break
                vectors_failed += 1
                logger.warning(f"  → Failed: {exc}")
            except Exception as exc:
                vectors_failed += 1
                logger.warning(f"  → Failed: {exc}")

            if i % SAVE_EVERY == 0:
                save_vectors(existing_vectors)
                logger.info(f"  Progress saved: {len(existing_vectors)} total vectors")

            await asyncio.sleep(RATE_LIMIT)

        save_vectors(existing_vectors)

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    logger.info("=" * 60)
    logger.info("SUMMARY")
    logger.info("=" * 60)

    final_profiles = load_profiles()
    final_vectors = load_vectors()

    for city, artists in all_city_artists.items():
        logger.info(f"  {city.title()} events: {len(artists)} artists")

    logger.info(f"Total unique event artists: {len(all_artists)}")
    logger.info(f"New SC profiles found: {found_count}")
    logger.info(f"Not found on SC: {not_found_count}")
    logger.info(f"New taste vectors scraped: {vectors_scraped}")
    logger.info(f"Taste vectors failed: {vectors_failed}")
    logger.info(f"Total DJ profiles: {len(final_profiles)}")
    logger.info(f"Total taste vectors: {len(final_vectors)}")


if __name__ == "__main__":
    asyncio.run(main())
