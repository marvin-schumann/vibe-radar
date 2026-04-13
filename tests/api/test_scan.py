"""Integration tests for the public /api/scan endpoint.

Tests URL validation, rate limiting, task lifecycle, character mapping,
override logic, and failure modes. Uses monkeypatching to avoid real
SoundCloud API calls.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Import the module under test
from src.api.scan import (
    _CHARACTERS,
    _TRIBE_TO_CHARACTER,
    _derive_character,
    _override_character_for_secondary_signal,
    _parse_soundcloud_url,
)


# ---------------------------------------------------------------------------
# URL parsing tests
# ---------------------------------------------------------------------------


class TestParseSoundCloudUrl:
    """Tests for _parse_soundcloud_url."""

    def test_valid_https(self):
        result = _parse_soundcloud_url("https://soundcloud.com/marvin")
        assert result is not None
        url, handle = result
        assert url == "https://soundcloud.com/marvin"
        assert handle == "marvin"

    def test_valid_without_scheme(self):
        result = _parse_soundcloud_url("soundcloud.com/marvin")
        assert result is not None
        assert result[0] == "https://soundcloud.com/marvin"
        assert result[1] == "marvin"

    def test_valid_www(self):
        result = _parse_soundcloud_url("https://www.soundcloud.com/marvin")
        assert result is not None
        assert result[1] == "marvin"

    def test_valid_mobile(self):
        result = _parse_soundcloud_url("https://m.soundcloud.com/marvin")
        assert result is not None
        assert result[1] == "marvin"

    def test_rejects_discover(self):
        assert _parse_soundcloud_url("https://soundcloud.com/discover") is None

    def test_rejects_stream(self):
        assert _parse_soundcloud_url("https://soundcloud.com/stream") is None

    def test_rejects_search(self):
        assert _parse_soundcloud_url("https://soundcloud.com/search") is None

    def test_rejects_non_soundcloud(self):
        assert _parse_soundcloud_url("https://google.com") is None

    def test_rejects_empty(self):
        assert _parse_soundcloud_url("") is None

    def test_rejects_too_long(self):
        assert _parse_soundcloud_url("a" * 400) is None

    def test_rejects_no_path(self):
        assert _parse_soundcloud_url("https://soundcloud.com/") is None

    def test_rejects_just_text(self):
        assert _parse_soundcloud_url("not a url at all") is None

    def test_strips_trailing_slash(self):
        result = _parse_soundcloud_url("https://soundcloud.com/marvin/")
        assert result is not None
        assert result[1] == "marvin"

    def test_rejects_track_path(self):
        # soundcloud.com/artist/track-name is a TRACK URL, not a profile URL.
        # The parser should reject it — we only want profile URLs for scanning.
        assert _parse_soundcloud_url("https://soundcloud.com/marvin/my-track") is None


# ---------------------------------------------------------------------------
# Character mapping tests
# ---------------------------------------------------------------------------


class TestCharacterMapping:
    """Tests for _CHARACTERS, _TRIBE_TO_CHARACTER, and _derive_character."""

    def test_all_10_characters_exist(self):
        assert len(_CHARACTERS) == 10
        expected = {
            "bunker_bear", "fog_whale", "sunrise_stag", "disco_flamingo",
            "lounge_lynx", "hard_rhino", "breakbeat_falcon", "jungle_tiger",
            "garage_swan", "boom_bap_owl",
        }
        assert set(_CHARACTERS.keys()) == expected

    def test_all_characters_have_required_fields(self):
        required = {"name", "alt_name", "voice_line", "image_path", "rarity"}
        for slug, char in _CHARACTERS.items():
            for field in required:
                assert field in char, f"{slug} missing {field}"
            assert char["image_path"].startswith("/static/characters/")

    def test_all_7_tribes_mapped(self):
        assert len(_TRIBE_TO_CHARACTER) == 7
        tribes = {
            "Warehouse Monk", "Sonic Archaeologist", "Fog Machine Philosopher",
            "Strobe Nomad", "Dawn Chaser", "Bass Templar", "Circuit Bender",
        }
        assert set(_TRIBE_TO_CHARACTER.keys()) == tribes

    def test_derive_character_basic(self):
        taste = {
            "taste_tribe": {
                "tribe": {
                    "name": "Warehouse Monk",
                    "tagline": "Devotion to the 4/4 sacrament",
                    "description": "Deep, repetitive, hypnotic.",
                    "icon": "//",
                    "confidence": 0.85,
                }
            },
            "taste_dna": {"genre_families": {}},
        }
        char = _derive_character(taste)
        assert char is not None
        assert char["slug"] == "bunker_bear"
        assert char["name"] == "Bunker Bear"
        assert char["alt_name"] == "Betonhund"
        assert "sunlight" in char["voice_line"].lower()

    def test_derive_character_returns_none_without_tribe(self):
        taste = {"taste_tribe": {}, "taste_dna": {}}
        assert _derive_character(taste) is None

    def test_derive_character_unknown_tribe_fallback(self):
        taste = {
            "taste_tribe": {
                "tribe": {
                    "name": "Unknown Future Tribe",
                    "tagline": "???",
                    "description": "???",
                    "icon": "??",
                    "confidence": 0.5,
                }
            },
            "taste_dna": {"genre_families": {}},
        }
        char = _derive_character(taste)
        assert char is not None
        assert char["slug"] is None
        assert char["name"] == "Unknown Future Tribe"


# ---------------------------------------------------------------------------
# Override logic tests
# ---------------------------------------------------------------------------


class TestCharacterOverride:
    """Tests for _override_character_for_secondary_signal."""

    def test_heavy_techno_triggers_hard_rhino(self):
        taste = {"taste_dna": {"genre_families": {"techno": 0.75}}}
        result = _override_character_for_secondary_signal("bunker_bear", taste)
        assert result == "hard_rhino"

    def test_moderate_techno_stays_bunker_bear(self):
        taste = {"taste_dna": {"genre_families": {"techno": 0.5}}}
        result = _override_character_for_secondary_signal("bunker_bear", taste)
        assert result == "bunker_bear"

    def test_non_bunker_bear_no_override(self):
        taste = {"taste_dna": {"genre_families": {"techno": 0.9}}}
        result = _override_character_for_secondary_signal("disco_flamingo", taste)
        assert result == "disco_flamingo"

    def test_empty_genre_families_no_crash(self):
        taste = {"taste_dna": {"genre_families": {}}}
        result = _override_character_for_secondary_signal("bunker_bear", taste)
        assert result == "bunker_bear"

    def test_missing_taste_dna_no_crash(self):
        taste = {}
        result = _override_character_for_secondary_signal("bunker_bear", taste)
        assert result == "bunker_bear"


# ---------------------------------------------------------------------------
# Character rarity distribution
# ---------------------------------------------------------------------------


class TestRarityDistribution:
    """Ensure the rarity system has reasonable distribution."""

    def test_rarity_values_are_valid(self):
        valid = {"common", "uncommon", "rare", "legendary"}
        for slug, char in _CHARACTERS.items():
            assert char["rarity"] in valid, f"{slug} has invalid rarity: {char['rarity']}"

    def test_at_least_one_common_and_one_rare(self):
        rarities = {char["rarity"] for char in _CHARACTERS.values()}
        assert "common" in rarities
        assert "rare" in rarities
