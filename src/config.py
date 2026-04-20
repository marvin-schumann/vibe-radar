"""Configuration management for Frequenz."""

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
    spotify_redirect_uri: str = ""  # set via env; falls back to {app_host}/auth/spotify/callback

    @property
    def effective_spotify_redirect_uri(self) -> str:
        return self.spotify_redirect_uri or f"{self.app_host}/auth/spotify/callback"

    # SoundCloud
    soundcloud_username: str = ""

    # Event APIs
    bandsintown_app_id: str = ""
    songkick_api_key: str = ""

    # Supabase
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # Admin / onboarding
    admin_secret_key: str = ""  # secret key for admin approval endpoint
    telegram_bot_token: str = ""  # Telegram bot token for signup notifications (legacy beta-only)
    telegram_chat_id: str = "1436217613"  # Marvin's Telegram chat ID

    # Brevo (transactional + waitlist + campaigns)
    brevo_api_key: str = ""
    brevo_sender_email: str = "hello@frequenz.live"
    brevo_sender_name: str = "Frequenz"
    brevo_waitlist_list_id: int = 3  # "Frequenz Waitlist" list created in Brevo dashboard
    brevo_admin_notification_email: str = "hello@frequenz.live"  # where signup notifications go
    brevo_doi_template_id: int = 0  # Brevo DOI confirmation template; 0 = not configured (fallback to direct add)
    brevo_doi_redirection_url: str = "https://frequenz.live/confirmed"  # URL user lands on after DOI click

    # App
    app_secret_key: str = "change-me-in-production"
    app_host: str = "http://localhost:8000"
    app_environment: str = "development"

    # App settings
    event_scrape_interval_hours: int = 6
    city: str = "Madrid"
    country: str = "Spain"
    match_threshold: float = 0.45  # Minimum confidence for vibe matches
    fuzzy_match_threshold: int = 85  # Minimum fuzz ratio for exact matches
    days_ahead: int = 90  # How far ahead to look for events


settings = Settings()
