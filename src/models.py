"""Shared data models for Frequenz."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class MusicSource(str, Enum):
    SPOTIFY = "spotify"
    SOUNDCLOUD = "soundcloud"


class EventSource(str, Enum):
    RESIDENT_ADVISOR = "resident_advisor"
    BANDSINTOWN = "bandsintown"
    SONGKICK = "songkick"
    XCEED = "xceed"
    INSTAGRAM = "instagram"


class MatchType(str, Enum):
    EXACT = "exact"  # Artist name matches directly
    VIBE = "vibe"  # Genre/style similarity match


class Artist(BaseModel):
    """An artist from the user's music library."""

    name: str
    normalized_name: str = ""  # Lowercase, stripped for matching
    genres: list[str] = Field(default_factory=list)
    source: MusicSource
    source_url: str | None = None
    image_url: str | None = None
    popularity: int | None = None  # 0-100 for Spotify
    play_count: int | None = None
    audio_features: AudioFeatures | None = None

    def model_post_init(self, __context: object) -> None:
        if not self.normalized_name:
            self.normalized_name = self.name.lower().strip()


class AudioFeatures(BaseModel):
    """Spotify audio features for taste profiling."""

    danceability: float = 0.0  # 0-1
    energy: float = 0.0  # 0-1
    tempo: float = 0.0  # BPM
    valence: float = 0.0  # 0-1 (musical positivity)
    acousticness: float = 0.0  # 0-1
    instrumentalness: float = 0.0  # 0-1
    liveness: float = 0.0  # 0-1
    speechiness: float = 0.0  # 0-1


class Venue(BaseModel):
    """An event venue."""

    name: str
    city: str = "Madrid"
    address: str | None = None
    url: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class Event(BaseModel):
    """An upcoming music event."""

    name: str
    artists: list[str] = Field(default_factory=list)
    normalized_artists: list[str] = Field(default_factory=list)
    venue: Venue | None = None
    date: datetime
    end_date: datetime | None = None
    url: str | None = None
    image_url: str | None = None
    source: EventSource
    genres: list[str] = Field(default_factory=list)
    description: str | None = None
    price: str | None = None

    def model_post_init(self, __context: object) -> None:
        if not self.normalized_artists and self.artists:
            self.normalized_artists = [a.lower().strip() for a in self.artists]


class Match(BaseModel):
    """A match between an artist and an event."""

    event: Event
    matched_artist: Artist
    event_artist_name: str  # The name as it appears on the event
    match_type: MatchType
    confidence: float = Field(ge=0.0, le=1.0)  # 0-1 confidence score
    match_reason: str = ""  # Human-readable explanation

    @property
    def sort_key(self) -> tuple[int, float, datetime]:
        """Sort by match type (exact first), then confidence, then date."""
        type_order = 0 if self.match_type == MatchType.EXACT else 1
        return (type_order, -self.confidence, self.event.date)


class TasteProfile(BaseModel):
    """Aggregated taste profile from all sources."""

    top_genres: list[tuple[str, int]] = Field(default_factory=list)  # (genre, count)
    avg_features: AudioFeatures | None = None
    features_estimated: bool = False  # True when avg_features is derived from genres
    total_artists: int = 0
    sources: dict[str, int] = Field(default_factory=dict)  # source -> artist count
    genre_clusters: list[list[str]] = Field(default_factory=list)
