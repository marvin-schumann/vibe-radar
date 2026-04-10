"""Unit tests for the Instagram event parser heuristics.

These tests are deliberately network-free and don't import instaloader.
They cover:

- looks_like_event(): the fast "is this even worth parsing?" filter
- _extract_date(): Spanish + English + numeric date formats, and the
  "roll forward one year" behaviour for dates that look like the past
- parse_post(): end-to-end on realistic fabricated Spanish techno captions
- Ticket URL extraction prefers known ticketing hosts
"""

from __future__ import annotations

from datetime import datetime

import pytest

from src.collectors.events.instagram_parser import (
    ParsedEvent,
    _extract_date,
    _extract_ticket_url,
    looks_like_event,
    parse_post,
)

# Fixed "now" so tests are deterministic — mid-April 2026
POSTED_AT = datetime(2026, 4, 10, 18, 0, 0)

KNOWN_VENUE_HANDLES = {"mondodisko_madrid", "goyasocialclub", "sala_but", "specka_club"}
KNOWN_DJS = ["Charlotte de Witte", "Amelie Lens", "I Hate Models", "Dax J"]


# ---------------------------------------------------------------------------
# looks_like_event
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "caption",
    [
        "VIERNES 25 ABRIL — techno marathon en Mondo Disko",
        "Sábado 26.04 · line up brutal",
        "Friday April 25 — doors at 23:30",
        "25/04 @sala_but presenta: I Hate Models",
        "Solo esta noche: Amelie Lens b2b Charlotte de Witte — 25 de abril",
    ],
)
def test_looks_like_event_positive(caption: str) -> None:
    assert looks_like_event(caption) is True


@pytest.mark.parametrize(
    "caption",
    [
        "New merch drop — link in bio",
        "Thanks for the madness last night. See you soon.",
        "",
    ],
)
def test_looks_like_event_negative(caption: str) -> None:
    assert looks_like_event(caption) is False


# ---------------------------------------------------------------------------
# _extract_date
# ---------------------------------------------------------------------------


def test_extract_numeric_date_dd_mm() -> None:
    d = _extract_date("Nos vemos el 25.04", posted_at=POSTED_AT)
    assert d == datetime(2026, 4, 25)


def test_extract_spanish_month_word() -> None:
    d = _extract_date("Viernes 25 de abril · doors 23:30", posted_at=POSTED_AT)
    assert d == datetime(2026, 4, 25)


def test_extract_english_month_word() -> None:
    d = _extract_date("Friday April 25 · techno all night", posted_at=POSTED_AT)
    assert d == datetime(2026, 4, 25)


def test_extract_date_rolls_forward_when_in_past() -> None:
    # Posted mid-April, caption says "1 de marzo" → must roll to 2027
    d = _extract_date("Save the date: 1 de marzo", posted_at=POSTED_AT)
    assert d == datetime(2027, 3, 1)


def test_extract_date_keeps_near_past_within_week() -> None:
    # Posted April 10, caption says "7.04" → recent past, keep 2026
    d = _extract_date("Fotos del 7.04 ya disponibles", posted_at=POSTED_AT)
    assert d == datetime(2026, 4, 7)


def test_extract_date_with_explicit_year() -> None:
    d = _extract_date("25 de abril de 2027", posted_at=POSTED_AT)
    assert d == datetime(2027, 4, 25)


def test_extract_date_returns_none_when_no_date() -> None:
    assert _extract_date("Check our new merch", posted_at=POSTED_AT) is None


# ---------------------------------------------------------------------------
# _extract_ticket_url
# ---------------------------------------------------------------------------


def test_ticket_url_prefers_known_host() -> None:
    caption = "Más info: https://example.com/about\nEntradas: https://wegow.com/ev/abc"
    assert _extract_ticket_url(caption) == "https://wegow.com/ev/abc"


def test_ticket_url_falls_back_to_first_url() -> None:
    caption = "Info completa en https://mondodisko.es/eventos/mayo"
    assert _extract_ticket_url(caption) == "https://mondodisko.es/eventos/mayo"


def test_ticket_url_none_when_no_urls() -> None:
    assert _extract_ticket_url("Solo esta noche — te esperamos") is None


# ---------------------------------------------------------------------------
# parse_post — end-to-end on realistic captions
# ---------------------------------------------------------------------------


def _parse(caption: str, *, default_venue: str | None = None) -> ParsedEvent:
    return parse_post(
        caption,
        posted_at=POSTED_AT,
        default_venue=default_venue,
        known_venue_handles=KNOWN_VENUE_HANDLES,
        known_djs=KNOWN_DJS,
        image_url="https://instagram.com/p/ABC/image.jpg",
        post_url="https://instagram.com/p/ABC/",
    )


def test_parse_venue_account_with_default_venue() -> None:
    caption = (
        "BINARY TECHNO x MONDO\n"
        "Viernes 25 de abril · 23:30 - 06:00\n"
        "Line up: Amelie Lens b2b Charlotte de Witte + Dax J\n"
        "Entradas: https://wegow.com/ev/binary-mondo-042"
    )
    pe = _parse(caption, default_venue="Mondo Disko")
    assert pe.name == "BINARY TECHNO x MONDO"
    assert pe.date == datetime(2026, 4, 25)
    assert pe.venue_name == "Mondo Disko"
    assert "Amelie Lens" in pe.lineup
    assert "Charlotte De Witte" in pe.lineup or "Charlotte de Witte" in pe.lineup
    assert "Dax J" in pe.lineup
    assert pe.ticket_url == "https://wegow.com/ev/binary-mondo-042"
    assert pe.is_complete_enough()


def test_parse_collective_post_extracts_venue_from_mention() -> None:
    caption = (
        "RAVENCLAW INVITES: I HATE MODELS\n"
        "Sábado 26.04 @mondodisko_madrid\n"
        "Doors 00:00 · https://ra.co/events/9999999"
    )
    pe = _parse(caption, default_venue=None)
    assert pe.date == datetime(2026, 4, 26)
    assert pe.venue_name is not None
    assert "mondodisko" in pe.venue_name.lower()
    assert pe.ticket_url == "https://ra.co/events/9999999"


def test_parse_drops_non_event_post() -> None:
    caption = "Thanks for the madness last night. Photos soon."
    pe = _parse(caption, default_venue="Mondo Disko")
    # No date → not complete
    assert pe.date is None
    assert pe.is_complete_enough() is False


def test_parse_event_with_no_venue_stays_incomplete() -> None:
    caption = "Save the date: 10 mayo — lineup tba"
    pe = _parse(caption, default_venue=None)
    assert pe.date == datetime(2026, 5, 10)
    assert pe.venue_name is None
    assert pe.is_complete_enough() is False


def test_parse_uses_dj_database_for_lineup() -> None:
    caption = (
        "HARDGROOVE NIGHT\n"
        "Viernes 25 abril · special guest: i hate models all night long\n"
        "@sala_but — entradas en https://dice.fm/event/abc"
    )
    pe = _parse(caption, default_venue="Sala But")
    # Case-insensitive DB match
    assert any(a.lower() == "i hate models" for a in pe.lineup)
    assert pe.ticket_url.startswith("https://dice.fm")


def test_parse_deduplicates_lineup() -> None:
    caption = (
        "TRIBAL\n"
        "Sábado 26 abril\n"
        "Amelie Lens + Amelie Lens (all night long)\n"
        "@goyasocialclub"
    )
    pe = _parse(caption, default_venue="Goya Social Club")
    lowered = [a.lower() for a in pe.lineup]
    assert lowered.count("amelie lens") == 1


def test_parse_strips_leading_emoji_from_name() -> None:
    caption = (
        "🔥🔥 BINARY // I HATE MODELS 🔥🔥\n"
        "Viernes 25.04 @mondodisko_madrid"
    )
    pe = _parse(caption)
    assert pe.name.startswith("BINARY")
    assert "🔥" not in pe.name
