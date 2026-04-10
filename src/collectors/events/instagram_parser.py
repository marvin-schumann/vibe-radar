"""Heuristic parser that turns an Instagram post into a (maybe) Event.

This module is deliberately dependency-free (no instaloader import) so we
can unit-test the parsing logic without network access.

Heuristics, in order:

1.  **Looks like an event?** — the caption has a Spanish/English date word
    ("viernes", "sábado", "jueves", "fri", "sat", …), a ``DD.MM`` / ``DD/MM``
    pattern, or a ``DD mes`` / ``DD month`` date. If none of those match
    AND the caption has no @venue mention, we bail.
2.  **Event name** — first non-empty line of the caption, truncated.
3.  **Date** — first matching Spanish or English date. Year defaults to
    the post's year; if the parsed date is in the past relative to the
    post, we roll forward one year.
4.  **Venue** — from ``default_venue`` if the source is a venue account;
    otherwise from an @venue mention (cross-referenced against a known
    venue handle set); otherwise parsed from an ``@`` fragment in the
    caption; otherwise ``None``.
5.  **Lineup** — @mentions that are NOT the venue, plus lines that look
    like DJ lineups (``Artist1 b2b Artist2``, ``Artist + Artist``, ...).
    Optionally intersected with the DJ database.
6.  **Ticket link** — first URL from caption matching a known ticketing
    host (wegow, ra.co, dice.fm, eventbrite, entradium, shotgun).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SPANISH_WEEKDAYS = {
    "lunes",
    "martes",
    "miércoles",
    "miercoles",
    "jueves",
    "viernes",
    "sábado",
    "sabado",
    "domingo",
}

ENGLISH_WEEKDAYS = {
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
    "mon",
    "tue",
    "tues",
    "wed",
    "thu",
    "thur",
    "thurs",
    "fri",
    "sat",
    "sun",
}

SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
    "ene": 1,
    "feb": 2,
    "mar": 3,
    "abr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "ago": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dic": 12,
}

ENGLISH_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

ALL_MONTHS: dict[str, int] = {**SPANISH_MONTHS, **ENGLISH_MONTHS}

TICKET_HOSTS = (
    "wegow.com",
    "ra.co",
    "dice.fm",
    "eventbrite.com",
    "eventbrite.es",
    "entradium.com",
    "shotgun.live",
    "fever.com",
    "xceed.me",
    "notikumi.com",
)

_URL_RE = re.compile(r"https?://[^\s\)\]]+", re.IGNORECASE)
_NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})[./-](\d{1,2})(?:[./-](\d{2,4}))?\b")
_MONTH_NAME_RE = re.compile(
    r"\b(\d{1,2})\s+(?:de\s+)?([A-Za-zÁÉÍÓÚáéíóúñÑ]+)(?:\s+(?:de\s+)?(\d{4}))?\b"
)
_REVERSE_MONTH_NAME_RE = re.compile(
    r"\b([A-Za-zÁÉÍÓÚáéíóúñÑ]+)\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(\d{4}))?\b"
)
_MENTION_RE = re.compile(r"@([A-Za-z0-9._]+)")

# Splits used for lineup extraction from a single line
_LINEUP_SPLIT_RE = re.compile(r"\s+b2b\s+|\s*\+\s*|\s*,\s*|\s*\|\s*|\s*/\s*", re.IGNORECASE)

# Non-DJ words commonly found after @ mentions that we want to drop
_VENUE_MENTION_NOISE = {"madrid", "spain", "españa", "espana"}


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class ParsedEvent:
    """What the parser extracts from one post. May be incomplete."""

    name: str
    date: datetime | None
    venue_name: str | None
    lineup: list[str] = field(default_factory=list)
    ticket_url: str | None = None
    image_url: str | None = None
    post_url: str | None = None
    raw_caption: str = ""

    def is_complete_enough(self) -> bool:
        """Minimum fields to persist: name + date + venue."""
        return bool(self.name and self.date and self.venue_name)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def looks_like_event(caption: str) -> bool:
    """Fast filter — is this post worth a full parse?"""
    if not caption:
        return False
    lowered = caption.lower()

    # 1. Spanish/English weekday word
    for word in SPANISH_WEEKDAYS | ENGLISH_WEEKDAYS:
        # Require word boundary to avoid "mar" matching inside "marzo"
        if re.search(rf"\b{re.escape(word)}\b", lowered):
            return True

    # 2. Numeric date pattern DD.MM / DD/MM / DD-MM
    if _NUMERIC_DATE_RE.search(caption):
        return True

    # 3. "12 abril" / "april 12" / "12 de abril"
    if _month_in_caption(lowered):
        return True

    return False


def parse_post(
    caption: str,
    *,
    posted_at: datetime,
    default_venue: str | None = None,
    known_venue_handles: Iterable[str] = (),
    known_djs: Iterable[str] = (),
    image_url: str | None = None,
    post_url: str | None = None,
) -> ParsedEvent:
    """Parse a single caption into a ParsedEvent (possibly incomplete)."""
    caption = caption or ""
    name = _extract_name(caption)
    date = _extract_date(caption, posted_at=posted_at)
    venue = _extract_venue(
        caption,
        default_venue=default_venue,
        known_venue_handles=set(h.lower() for h in known_venue_handles),
    )
    lineup = _extract_lineup(
        caption,
        known_djs=set(d.lower() for d in known_djs),
        venue_handles=set(h.lower() for h in known_venue_handles),
    )
    ticket = _extract_ticket_url(caption)

    return ParsedEvent(
        name=name,
        date=date,
        venue_name=venue,
        lineup=lineup,
        ticket_url=ticket,
        image_url=image_url,
        post_url=post_url,
        raw_caption=caption,
    )


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------


def _month_in_caption(lowered: str) -> bool:
    if _MONTH_NAME_RE.search(lowered) or _REVERSE_MONTH_NAME_RE.search(lowered):
        # Confirm the matched word is really a known month
        for m in _MONTH_NAME_RE.finditer(lowered):
            if m.group(2).lower() in ALL_MONTHS:
                return True
        for m in _REVERSE_MONTH_NAME_RE.finditer(lowered):
            if m.group(1).lower() in ALL_MONTHS:
                return True
    return False


def _extract_name(caption: str) -> str:
    """First non-empty line, stripped of leading emojis and marketing fluff."""
    for raw_line in caption.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        # Drop leading emoji run + common attention-grabbing symbols
        line = re.sub(r"^[^\w@#]+", "", line).strip()
        if not line:
            continue
        # Trim trailing emoji / decorative symbol run
        line = re.sub(r"[^\w\s.!?)'\"-]+$", "", line).strip()
        # Trim trailing hashtag storms
        line = re.sub(r"(\s+#\w+){2,}.*$", "", line).strip()
        # Cap length
        return line[:120]
    return ""


def _extract_date(caption: str, *, posted_at: datetime) -> datetime | None:
    """Return the first plausible future date mentioned in the caption."""
    # 1. Numeric DD.MM
    for match in _NUMERIC_DATE_RE.finditer(caption):
        day, month, year = match.groups()
        try:
            day_i = int(day)
            month_i = int(month)
        except ValueError:
            continue
        if not (1 <= day_i <= 31 and 1 <= month_i <= 12):
            continue
        year_i = _normalize_year(year, posted_at.year)
        candidate = _safe_datetime(year_i, month_i, day_i)
        if candidate is None:
            continue
        return _roll_forward_if_past(candidate, posted_at)

    # 2. "12 abril" / "12 de abril"
    for match in _MONTH_NAME_RE.finditer(caption):
        day_str, month_word, year_str = match.groups()
        month_key = month_word.lower()
        if month_key not in ALL_MONTHS:
            continue
        try:
            day_i = int(day_str)
        except ValueError:
            continue
        if not (1 <= day_i <= 31):
            continue
        month_i = ALL_MONTHS[month_key]
        year_i = _normalize_year(year_str, posted_at.year)
        candidate = _safe_datetime(year_i, month_i, day_i)
        if candidate is None:
            continue
        return _roll_forward_if_past(candidate, posted_at)

    # 3. "April 12" / "Apr 12th, 2026"
    for match in _REVERSE_MONTH_NAME_RE.finditer(caption):
        month_word, day_str, year_str = match.groups()
        month_key = month_word.lower()
        if month_key not in ALL_MONTHS:
            continue
        try:
            day_i = int(day_str)
        except ValueError:
            continue
        if not (1 <= day_i <= 31):
            continue
        month_i = ALL_MONTHS[month_key]
        year_i = _normalize_year(year_str, posted_at.year)
        candidate = _safe_datetime(year_i, month_i, day_i)
        if candidate is None:
            continue
        return _roll_forward_if_past(candidate, posted_at)

    return None


def _normalize_year(year_str: str | None, fallback: int) -> int:
    if not year_str:
        return fallback
    try:
        y = int(year_str)
    except ValueError:
        return fallback
    if y < 100:
        y += 2000
    return y


def _safe_datetime(year: int, month: int, day: int) -> datetime | None:
    try:
        return datetime(year, month, day)
    except ValueError:
        return None


def _roll_forward_if_past(candidate: datetime, posted_at: datetime) -> datetime:
    """If candidate is more than 7 days before posted_at, bump by one year."""
    posted_naive = posted_at.replace(tzinfo=None)
    delta = (candidate - posted_naive).days
    if delta < -7:
        try:
            return candidate.replace(year=candidate.year + 1)
        except ValueError:  # e.g. Feb 29 → next year is not a leap year
            return candidate.replace(year=candidate.year + 1, day=28)
    return candidate


def _extract_venue(
    caption: str,
    *,
    default_venue: str | None,
    known_venue_handles: set[str],
) -> str | None:
    if default_venue:
        return default_venue
    mentions = [m.lower() for m in _MENTION_RE.findall(caption)]
    for m in mentions:
        if m in known_venue_handles:
            # Return a human-ish name: strip underscores + title case
            return _humanize_handle(m)
    # Look for a literal "en @handle" or "@handle" pattern
    for m in mentions:
        if m in _VENUE_MENTION_NOISE:
            continue
        # Heuristic: anything mentioned + containing "club", "sala", "room"
        if any(kw in m for kw in ("club", "sala", "room", "disko", "fabrik", "cafe", "palma")):
            return _humanize_handle(m)
    return None


def _humanize_handle(handle: str) -> str:
    cleaned = handle.replace("_", " ").replace(".", " ").strip()
    return " ".join(w.capitalize() for w in cleaned.split())


def _extract_lineup(
    caption: str,
    *,
    known_djs: set[str],
    venue_handles: set[str],
) -> list[str]:
    """Lineup = DJ-database mentions first, then non-venue @mentions,
    then heuristic lineup-line splits. Order preserved, dedupe case-insensitive."""
    found: list[str] = []
    seen: set[str] = set()

    def _add(name: str) -> None:
        cleaned = name.strip()
        if not cleaned:
            return
        key = cleaned.lower()
        if key in seen:
            return
        if key in _VENUE_MENTION_NOISE or key in venue_handles:
            return
        seen.add(key)
        found.append(cleaned)

    # 1. Caption text matches against known DJ names (whole-word)
    lowered = caption.lower()
    for dj in known_djs:
        if not dj:
            continue
        if re.search(rf"\b{re.escape(dj)}\b", lowered):
            _add(dj.title() if dj.islower() else dj)

    # 2. @mentions that aren't a venue
    for mention in _MENTION_RE.findall(caption):
        if mention.lower() in venue_handles:
            continue
        _add(_humanize_handle(mention))

    # 3. Lineup-line heuristic: a line with "b2b" or "+" and no sentence punctuation
    for line in caption.splitlines():
        s = line.strip()
        if not s or len(s) > 160:
            continue
        if re.search(r"\bb2b\b", s, re.IGNORECASE) or " + " in s:
            for part in _LINEUP_SPLIT_RE.split(s):
                token = re.sub(r"[^\w\s&'.-]", "", part).strip()
                # Drop tokens that look like dates or weekdays
                if not token:
                    continue
                if token.lower() in SPANISH_WEEKDAYS | ENGLISH_WEEKDAYS:
                    continue
                if _NUMERIC_DATE_RE.search(token):
                    continue
                if len(token) < 2 or len(token) > 40:
                    continue
                _add(token)

    return found


def _extract_ticket_url(caption: str) -> str | None:
    urls = _URL_RE.findall(caption)
    for url in urls:
        url = url.rstrip(".,;")
        lowered = url.lower()
        for host in TICKET_HOSTS:
            if host in lowered:
                return url
    # Fallback: first URL if nothing matches a known host
    return urls[0].rstrip(".,;") if urls else None
