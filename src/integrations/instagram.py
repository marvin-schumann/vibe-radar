"""Instagram scraping wrapper.

Thin, testable wrapper around ``instaloader`` that:

- fetches the latest N posts from a public profile (anonymous, no login)
- enforces polite rate-limiting (random sleeps between requests)
- retries on transient / rate-limit errors with exponential backoff
- raises well-typed errors for the "private account" / "profile not found"
  cases so the caller can log + skip gracefully

Why instaloader?
----------------
For a solo founder, instaloader is the sweet spot:

- free, pure-Python, no API key, maintained on GitHub
- exposes a clean iterator over a profile's posts
- hands you caption, date, image url, mention list, hashtag list, location

The trade-off is that Instagram can rate-limit or IP-ban aggressive
anonymous scrapers. For ~15 accounts x 15 posts = ~225 requests per day
with 5-15s random delays (≈30-60 min total), we're well under the pain
threshold. If/when we start seeing `ConnectionException` / 429s, the
upgrade path is Apify's Instagram scraper (~$20-40/mo, handles proxies
and rotation) — swap this module's `fetch_latest_posts` implementation
and keep the rest of the pipeline unchanged.

Nothing in this module imports instaloader at module scope; the import
is deferred to ``_get_instaloader`` so the unit tests for the parser
don't need the dependency installed.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from loguru import logger


class InstagramError(Exception):
    """Base class for all scraper errors."""


class PrivateAccountError(InstagramError):
    """The target profile is private — anonymous scraping can't read posts."""


class ProfileNotFoundError(InstagramError):
    """The target profile doesn't exist (or was renamed)."""


class RateLimitError(InstagramError):
    """Instagram returned a rate-limit / 429 / connection block."""


@dataclass
class InstagramPost:
    """Normalized Instagram post — the only shape the rest of the pipeline sees.

    Kept deliberately minimal so we can swap the underlying library
    (instaloader → Apify → Playwright) without touching the parser.
    """

    shortcode: str
    caption: str
    posted_at: datetime
    image_url: str | None
    post_url: str
    mentioned_users: list[str]
    hashtags: list[str]
    location: str | None
    is_video: bool

    @property
    def first_line(self) -> str:
        """First non-empty line of the caption — heuristic "event name"."""
        for line in self.caption.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped
        return ""


# ---------------------------------------------------------------------------
# instaloader adapter
# ---------------------------------------------------------------------------


def _get_instaloader() -> Any:
    """Deferred import so tests for the parser don't need the dep installed."""
    try:
        import instaloader  # type: ignore
    except ImportError as exc:  # pragma: no cover - trivial
        raise InstagramError(
            "instaloader not installed. Run: pip install instaloader"
        ) from exc
    return instaloader


def _build_loader() -> Any:
    """Construct a low-footprint Instaloader instance for anonymous reads."""
    il = _get_instaloader()
    return il.Instaloader(
        download_pictures=False,
        download_videos=False,
        download_video_thumbnails=False,
        download_geotags=False,
        download_comments=False,
        save_metadata=False,
        compress_json=False,
        post_metadata_txt_pattern="",
        quiet=True,
    )


def _post_to_domain(post: Any, handle: str) -> InstagramPost:
    """Convert an instaloader Post into our InstagramPost dataclass."""
    caption = post.caption or ""
    shortcode = post.shortcode
    return InstagramPost(
        shortcode=shortcode,
        caption=caption,
        posted_at=post.date_utc,
        image_url=getattr(post, "url", None),
        post_url=f"https://www.instagram.com/p/{shortcode}/",
        mentioned_users=list(post.caption_mentions or []),
        hashtags=list(post.caption_hashtags or []),
        location=getattr(post.location, "name", None) if post.location else None,
        is_video=bool(getattr(post, "is_video", False)),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_latest_posts(
    handle: str,
    *,
    limit: int = 15,
    min_delay: float = 5.0,
    max_delay: float = 15.0,
    retry_max_attempts: int = 3,
    retry_base_seconds: float = 60.0,
    _loader: Any = None,
    _sleep: Any = time.sleep,
) -> list[InstagramPost]:
    """Fetch the latest ``limit`` posts from a public IG profile.

    Args:
        handle: profile handle without the leading ``@``.
        limit: max posts to return (iteration stops once reached).
        min_delay, max_delay: polite random sleep between posts.
        retry_max_attempts: retries on ConnectionException / 429.
        retry_base_seconds: first backoff sleep. Doubles each attempt.
        _loader, _sleep: test hooks — prod callers should not pass these.

    Raises:
        PrivateAccountError, ProfileNotFoundError, RateLimitError, InstagramError.
    """
    il = _get_instaloader()
    loader = _loader or _build_loader()

    attempt = 0
    while True:
        attempt += 1
        try:
            profile = il.Profile.from_username(loader.context, handle)
            if profile.is_private:
                raise PrivateAccountError(f"@{handle} is private — skipping")

            posts: list[InstagramPost] = []
            for post in profile.get_posts():
                posts.append(_post_to_domain(post, handle))
                if len(posts) >= limit:
                    break
                # polite delay between individual post fetches
                _sleep(random.uniform(min_delay, max_delay))
            return posts

        except il.exceptions.ProfileNotExistsException as exc:
            raise ProfileNotFoundError(f"@{handle} does not exist") from exc
        except il.exceptions.LoginRequiredException as exc:
            # Instagram asks for login → treat like private for v1
            raise PrivateAccountError(
                f"@{handle} requires login (effectively private)"
            ) from exc
        except il.exceptions.ConnectionException as exc:
            msg = str(exc).lower()
            is_rate_limit = (
                "429" in msg
                or "please wait" in msg
                or "rate" in msg
                or "temporarily" in msg
            )
            if attempt >= retry_max_attempts:
                if is_rate_limit:
                    raise RateLimitError(
                        f"@{handle} rate-limited after {attempt} attempts: {exc}"
                    ) from exc
                raise InstagramError(
                    f"@{handle} connection failed after {attempt} attempts: {exc}"
                ) from exc
            sleep_for = retry_base_seconds * (2 ** (attempt - 1))
            logger.warning(
                f"[instagram] @{handle} connection error (attempt "
                f"{attempt}/{retry_max_attempts}): {exc} — sleeping {sleep_for}s"
            )
            _sleep(sleep_for)
            continue
