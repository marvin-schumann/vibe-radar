"""Configuration management for Vibe Radar."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).parent.parent / ".env"),
        env_file_encoding="utf-8",
    )

    # Spotify
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = "http://localhost:8888/callback"

    # SoundCloud
    soundcloud_username: str = ""

    # Event APIs
    bandsintown_app_id: str = ""
    songkick_api_key: str = ""

    # App settings
    city: str = "Madrid"
    country: str = "Spain"
    match_threshold: float = 0.6  # Minimum confidence for vibe matches
    fuzzy_match_threshold: int = 85  # Minimum fuzz ratio for exact matches
    days_ahead: int = 30  # How far ahead to look for events


settings = Settings()
