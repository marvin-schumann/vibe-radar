"""Spotify collector — gathers artist and audio taste data via the Spotify Web API."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import spotipy
from loguru import logger
from spotipy import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from src.config import settings
from src.models import Artist, AudioFeatures, MusicSource

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
TOKEN_CACHE_PATH = PROJECT_ROOT / ".spotify_cache"

SCOPES = " ".join(
    [
        "user-top-read",
        "user-follow-read",
        "user-read-recently-played",
        "user-library-read",
    ]
)

TIME_RANGES = ("short_term", "medium_term", "long_term")

# Retry / pagination
MAX_RETRIES = 3
RETRY_BACKOFF = 1.0  # seconds, doubled each retry
SAVED_TRACKS_LIMIT = 200  # cap how many saved tracks we scan
RECENTLY_PLAYED_LIMIT = 50  # Spotify max for this endpoint


class SpotifyCollector:
    """Collects the user's music taste data from Spotify."""

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        auth_manager = SpotifyOAuth(
            client_id=settings.spotify_client_id,
            client_secret=settings.spotify_client_secret,
            redirect_uri=settings.spotify_redirect_uri,
            scope=SCOPES,
            cache_path=str(TOKEN_CACHE_PATH),
        )
        self.sp = spotipy.Spotify(auth_manager=auth_manager, retries=0)
        logger.info("SpotifyCollector initialised (cache: {})", TOKEN_CACHE_PATH)

    @classmethod
    def from_token(cls, access_token: str) -> "SpotifyCollector":
        """Create a collector using an existing access token (no local cache)."""
        obj = cls.__new__(cls)
        obj.sp = spotipy.Spotify(auth=access_token, retries=0)
        logger.info("SpotifyCollector initialised from provided token")
        return obj

    @classmethod
    def from_tokens(
        cls,
        access_token: str,
        refresh_token: str | None,
        cache_handler: Any | None = None,
    ) -> "SpotifyCollector":
        """Create a collector with auto-refresh support via a CacheHandler.

        If a cache_handler is supplied (must implement spotipy CacheHandler),
        spotipy will auto-refresh the access token when it expires and call
        cache_handler.save_token_to_cache() with the new token_info.
        """
        obj = cls.__new__(cls)
        if refresh_token and cache_handler:
            # Seed the cache with an already-expired token_info so spotipy
            # immediately tries to refresh on the first API call.
            token_info: dict[str, Any] = {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "expires_at": int(time.time()) - 1,
                "scope": SCOPES,
            }
            cache_handler.save_token_to_cache(token_info)
            auth_manager = SpotifyOAuth(
                client_id=settings.spotify_client_id,
                client_secret=settings.spotify_client_secret,
                redirect_uri=settings.effective_spotify_redirect_uri,
                scope=SCOPES,
                cache_handler=cache_handler,
            )
            obj.sp = spotipy.Spotify(auth_manager=auth_manager, retries=0)
            logger.info("SpotifyCollector initialised with token auto-refresh")
        else:
            obj.sp = spotipy.Spotify(auth=access_token, retries=0)
            logger.info("SpotifyCollector initialised from provided token (no refresh)")
        return obj

    # ------------------------------------------------------------------
    # Public async interface
    # ------------------------------------------------------------------

    async def collect_artists(self) -> list[Artist]:
        """Gather artists from every available Spotify endpoint and deduplicate."""

        artists: list[Artist] = []

        # 1. Top artists across all time ranges (best signal — what you actually listen to)
        for time_range in TIME_RANGES:
            artists.extend(self._fetch_top_artists(time_range))

        # 2. Followed artists — these are artists the user explicitly chose to
        # follow. With strict matching (no partial_ratio), having a large pool
        # is fine — only true name matches get through.
        artists.extend(self._fetch_followed_artists())

        # 3. Recently played tracks -> unique artists (no extra API calls)
        artists.extend(self._fetch_recently_played_artists())

        # 4. Liked tracks -> unique artists (capped at 100 tracks, no extra API calls)
        artists.extend(self._fetch_saved_track_artists())

        deduplicated = self._deduplicate(artists)
        logger.info(
            "Collected {} unique artists from Spotify ({} raw)",
            len(deduplicated),
            len(artists),
        )
        return deduplicated

    async def get_audio_profile(self) -> AudioFeatures:
        """Build an aggregated AudioFeatures profile from the user's top tracks."""

        track_ids: list[str] = []
        for time_range in TIME_RANGES:
            track_ids.extend(self._fetch_top_track_ids(time_range))

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique_ids: list[str] = []
        for tid in track_ids:
            if tid not in seen:
                seen.add(tid)
                unique_ids.append(tid)

        if not unique_ids:
            logger.warning("No top tracks found — returning default AudioFeatures")
            return AudioFeatures()

        features = self._fetch_audio_features(unique_ids)
        profile = self._average_features(features)
        logger.info(
            "Audio profile built from {} tracks (energy={:.2f}, tempo={:.0f})",
            len(features),
            profile.energy,
            profile.tempo,
        )
        return profile

    # ------------------------------------------------------------------
    # Private: top artists
    # ------------------------------------------------------------------

    def _fetch_top_artists(self, time_range: str, max_pages: int = 5) -> list[Artist]:
        """Fetch the user's top artists for the given time range.

        Paginates up to max_pages (default 5 × 50 = 250 per time range).
        All results are artists Spotify knows you actually listen to, ranked
        by play frequency — even page 5 is signal, not noise.
        """
        artists: list[Artist] = []
        offset = 0
        limit = 50

        for _ in range(max_pages):
            data = self._api_call(
                self.sp.current_user_top_artists,
                limit=limit,
                offset=offset,
                time_range=time_range,
            )
            if data is None:
                break

            items: list[dict[str, Any]] = data.get("items", [])
            if not items:
                break

            for item in items:
                artists.append(self._artist_from_spotify(item))

            if data.get("next") is None:
                break
            offset += limit

        logger.debug("Top artists ({}): {} found", time_range, len(artists))
        return artists

    # ------------------------------------------------------------------
    # Private: followed artists
    # ------------------------------------------------------------------

    def _fetch_followed_artists(self) -> list[Artist]:
        """Fetch all artists the user follows."""

        artists: list[Artist] = []
        after: str | None = None
        limit = 50

        while True:
            data = self._api_call(
                self.sp.current_user_followed_artists, limit=limit, after=after
            )
            if data is None:
                break

            artists_data = data.get("artists", {})
            items: list[dict[str, Any]] = artists_data.get("items", [])
            if not items:
                break

            for item in items:
                artists.append(self._artist_from_spotify(item))

            cursors = artists_data.get("cursors", {})
            after = cursors.get("after")
            if after is None:
                break

        logger.debug("Followed artists: {} found", len(artists))
        return artists

    # ------------------------------------------------------------------
    # Private: recently played
    # ------------------------------------------------------------------

    def _fetch_recently_played_artists(self) -> list[Artist]:
        """Extract unique artists from recently played tracks.

        Uses brief artist data from the track object — no extra per-artist API
        calls, so this never triggers rate limits.
        """
        data = self._api_call(
            self.sp.current_user_recently_played, limit=RECENTLY_PLAYED_LIMIT
        )
        if data is None:
            return []

        seen_ids: set[str] = set()
        artists: list[Artist] = []

        for item in data.get("items", []):
            track = item.get("track", {})
            for artist_brief in track.get("artists", []):
                artist_id = artist_brief.get("id")
                if artist_id and artist_id not in seen_ids:
                    seen_ids.add(artist_id)
                    artists.append(self._artist_from_brief(artist_brief))

        logger.debug("Recently played artists: {} unique", len(artists))
        return artists

    # ------------------------------------------------------------------
    # Private: saved / liked tracks
    # ------------------------------------------------------------------

    def _fetch_saved_track_artists(self) -> list[Artist]:
        """Extract unique artists from the user's saved (liked) tracks.

        Capped at 100 tracks (2 pages × 50). Uses brief artist data from each
        track — no per-artist API calls, so no rate limit risk. Artists won't
        have genre data but are included for name-based event matching.
        """
        seen_ids: set[str] = set()
        artists: list[Artist] = []
        offset = 0
        limit = 50
        max_tracks = 100

        while offset < max_tracks:
            data = self._api_call(
                self.sp.current_user_saved_tracks, limit=limit, offset=offset
            )
            if data is None:
                break

            items = data.get("items", [])
            if not items:
                break

            for item in items:
                track = item.get("track", {})
                for artist_brief in track.get("artists", []):
                    artist_id = artist_brief.get("id")
                    if artist_id and artist_id not in seen_ids:
                        seen_ids.add(artist_id)
                        artists.append(self._artist_from_brief(artist_brief))

            if data.get("next") is None:
                break
            offset += limit

        logger.debug("Saved-track artists: {} unique", len(artists))
        return artists

    # ------------------------------------------------------------------
    # Private: top tracks & audio features
    # ------------------------------------------------------------------

    def _fetch_top_track_ids(self, time_range: str) -> list[str]:
        """Return track IDs from the user's top tracks for a time range."""

        ids: list[str] = []
        offset = 0
        limit = 50

        while True:
            data = self._api_call(
                self.sp.current_user_top_tracks,
                limit=limit,
                offset=offset,
                time_range=time_range,
            )
            if data is None:
                break

            items = data.get("items", [])
            if not items:
                break

            for track in items:
                track_id = track.get("id")
                if track_id:
                    ids.append(track_id)

            if data.get("next") is None:
                break
            offset += limit

        return ids

    def _fetch_audio_features(self, track_ids: list[str]) -> list[dict[str, Any]]:
        """Fetch audio features for a list of track IDs (batched by 100)."""

        all_features: list[dict[str, Any]] = []

        for i in range(0, len(track_ids), 100):
            batch = track_ids[i : i + 100]
            result = self._api_call(self.sp.audio_features, tracks=batch)
            if result is None:
                continue
            for feat in result:
                if feat is not None:
                    all_features.append(feat)

        return all_features

    @staticmethod
    def _average_features(features: list[dict[str, Any]]) -> AudioFeatures:
        """Compute the mean of each audio feature across all tracks."""

        if not features:
            return AudioFeatures()

        keys = [
            "danceability",
            "energy",
            "tempo",
            "valence",
            "acousticness",
            "instrumentalness",
            "liveness",
            "speechiness",
        ]

        totals: dict[str, float] = {k: 0.0 for k in keys}
        count = len(features)

        for feat in features:
            for k in keys:
                totals[k] += float(feat.get(k, 0.0))

        return AudioFeatures(**{k: totals[k] / count for k in keys})

    # ------------------------------------------------------------------
    # Private: helpers
    # ------------------------------------------------------------------

    def _fetch_full_artist(self, artist_id: str) -> Artist | None:
        """Fetch full artist details by ID and return an Artist model."""

        data = self._api_call(self.sp.artist, artist_id=artist_id)
        if data is None:
            return None
        return self._artist_from_spotify(data)

    @staticmethod
    def _artist_from_brief(brief: dict[str, Any]) -> Artist:
        """Build a minimal Artist from a track's brief artist object.

        Track responses only include id/name/href — no genres or popularity.
        This is enough for name-based matching without any extra API calls.
        """
        external_urls = brief.get("external_urls", {})
        return Artist(
            name=brief.get("name", "Unknown"),
            genres=[],
            source=MusicSource.SPOTIFY,
            source_url=external_urls.get("spotify"),
        )

    @staticmethod
    def _artist_from_spotify(data: dict[str, Any]) -> Artist:
        """Convert a Spotify artist dict into an Artist model."""

        images = data.get("images", [])
        image_url = images[0]["url"] if images else None

        external_urls = data.get("external_urls", {})
        source_url = external_urls.get("spotify")

        return Artist(
            name=data.get("name", "Unknown"),
            genres=data.get("genres", []),
            source=MusicSource.SPOTIFY,
            source_url=source_url,
            image_url=image_url,
            popularity=data.get("popularity"),
        )

    @staticmethod
    def _deduplicate(artists: list[Artist]) -> list[Artist]:
        """Merge artists by normalized name, combining genres from all occurrences."""

        by_name: dict[str, Artist] = {}

        for artist in artists:
            key = artist.normalized_name
            if key in by_name:
                existing = by_name[key]
                # Merge genres (preserve order, no duplicates)
                merged_genres = list(existing.genres)
                for g in artist.genres:
                    if g not in merged_genres:
                        merged_genres.append(g)
                existing.genres = merged_genres
                # Keep the higher popularity value
                if artist.popularity is not None:
                    if existing.popularity is None or artist.popularity > existing.popularity:
                        existing.popularity = artist.popularity
                # Keep image / url if the existing one is missing
                if existing.image_url is None and artist.image_url is not None:
                    existing.image_url = artist.image_url
                if existing.source_url is None and artist.source_url is not None:
                    existing.source_url = artist.source_url
            else:
                by_name[key] = artist.model_copy()

        return list(by_name.values())

    # ------------------------------------------------------------------
    # Private: resilient API wrapper
    # ------------------------------------------------------------------

    def _api_call(self, func: Any, *args: Any, **kwargs: Any) -> Any:
        """Call a spotipy method, skipping immediately on rate limits.

        429 errors are not retried — spotipy dev apps hit aggressive rate limits
        and sleeping would block FastAPI's event loop. We skip the call and move on.
        """
        try:
            return func(*args, **kwargs)
        except SpotifyException as exc:
            if exc.http_status == 429:
                logger.warning("Spotify rate limit (429) — skipping call")
                return None
            if exc.http_status == 401:
                logger.warning("Spotify token expired (401) — skipping call")
                return None
            logger.error("Spotify API error {}: {}", exc.http_status, exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.error("Unexpected error calling Spotify API: {}", exc)
            return None
