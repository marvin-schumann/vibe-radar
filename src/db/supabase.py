"""Supabase client and database helpers for Vibe Radar."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from loguru import logger
from supabase import Client, create_client

from src.config import settings

# ─────────────────────────────────────────
# Client factory
# ─────────────────────────────────────────

_client: Client | None = None
_admin_client: Client | None = None


def get_client() -> Client:
    """Return a Supabase client using the anon key (respects RLS)."""
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_anon_key)
    return _client


def get_admin_client() -> Client:
    """Return a Supabase client using the service role key (bypasses RLS).
    Use only for background jobs and admin operations — never expose to users.
    """
    global _admin_client
    if _admin_client is None:
        _admin_client = create_client(settings.supabase_url, settings.supabase_service_role_key)
    return _admin_client


# ─────────────────────────────────────────
# Profiles
# ─────────────────────────────────────────

def get_profile(user_id: str) -> dict[str, Any] | None:
    db = get_admin_client()
    resp = db.table("profiles").select("*").eq("id", user_id).single().execute()
    return resp.data


def is_approved(user_id: str) -> bool:
    profile = get_profile(user_id)
    return bool(profile and profile.get("is_approved"))


def is_pro(user_id: str) -> bool:
    profile = get_profile(user_id)
    return bool(profile and profile.get("is_pro"))


def set_pro(user_id: str, value: bool) -> None:
    db = get_admin_client()
    db.table("profiles").update({"is_pro": value}).eq("id", user_id).execute()
    logger.info("Set is_pro={} for user {}", value, user_id)


def set_approved(user_id: str, value: bool = True) -> None:
    db = get_admin_client()
    db.table("profiles").update({"is_approved": value}).eq("id", user_id).execute()
    logger.info("Set is_approved={} for user {}", value, user_id)


def set_lemon_squeezy_customer(user_id: str, customer_id: str) -> None:
    db = get_admin_client()
    db.table("profiles").update({"lemon_squeezy_customer_id": customer_id}).eq("id", user_id).execute()


# ─────────────────────────────────────────
# Connected accounts (OAuth tokens)
# ─────────────────────────────────────────

def get_connected_account(user_id: str, platform: str) -> dict[str, Any] | None:
    db = get_admin_client()
    resp = (
        db.table("connected_accounts")
        .select("*")
        .eq("user_id", user_id)
        .eq("platform", platform)
        .single()
        .execute()
    )
    return resp.data


def upsert_connected_account(
    user_id: str,
    platform: str,
    *,
    access_token: str | None = None,
    refresh_token: str | None = None,
    token_expires_at: datetime | None = None,
    username: str | None = None,
) -> None:
    db = get_admin_client()
    payload: dict[str, Any] = {
        "user_id": user_id,
        "platform": platform,
        "last_synced": datetime.utcnow().isoformat(),
    }
    if access_token is not None:
        payload["access_token"] = access_token
    if refresh_token is not None:
        payload["refresh_token"] = refresh_token
    if token_expires_at is not None:
        payload["token_expires_at"] = token_expires_at.isoformat()
    if username is not None:
        payload["username"] = username

    db.table("connected_accounts").upsert(payload, on_conflict="user_id,platform").execute()


# ─────────────────────────────────────────
# User artists
# ─────────────────────────────────────────

def get_user_artists(user_id: str) -> list[dict[str, Any]]:
    db = get_admin_client()
    resp = db.table("user_artists").select("*").eq("user_id", user_id).execute()
    return resp.data or []


def upsert_user_artists(user_id: str, artists: list[dict[str, Any]]) -> None:
    """Upsert a batch of artists for a user.

    Each dict must have: platform, artist_id, name.
    Optional: genres (list[str]), image_url.
    """
    if not artists:
        return
    db = get_admin_client()
    rows = [
        {
            "user_id": user_id,
            "platform": a["platform"],
            "artist_id": a["artist_id"],
            "name": a["name"],
            "genres": a.get("genres", []),
            "image_url": a.get("image_url"),
            "last_synced": datetime.utcnow().isoformat(),
        }
        for a in artists
    ]
    db.table("user_artists").upsert(rows, on_conflict="user_id,platform,artist_id").execute()
    logger.info("Upserted {} artists for user {}", len(rows), user_id)


# ─────────────────────────────────────────
# Events (shared, scraped by background jobs)
# ─────────────────────────────────────────

def upsert_events(events: list[dict[str, Any]]) -> int:
    """Upsert scraped events. Returns number inserted/updated."""
    if not events:
        return 0
    db = get_admin_client()
    db.table("events").upsert(events, on_conflict="source,source_url").execute()
    logger.info("Upserted {} events", len(events))
    return len(events)


def get_events_for_city(city_id: str, days_ahead: int = 90) -> list[dict[str, Any]]:
    from datetime import date, timedelta
    db = get_admin_client()
    cutoff = (date.today() + timedelta(days=days_ahead)).isoformat()
    resp = (
        db.table("events")
        .select("*")
        .eq("city_id", city_id)
        .gte("date", date.today().isoformat())
        .lte("date", cutoff)
        .order("date")
        .execute()
    )
    return resp.data or []


# ─────────────────────────────────────────
# User matches
# ─────────────────────────────────────────

def get_user_matches(user_id: str) -> list[dict[str, Any]]:
    db = get_admin_client()
    resp = (
        db.table("user_matches")
        .select("*, events(*)")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .execute()
    )
    return resp.data or []


def upsert_user_matches(user_id: str, matches: list[dict[str, Any]]) -> None:
    """Upsert computed matches for a user.

    Each dict must have: event_id, match_type, confidence.
    Optional: matched_artist_name, match_reason.
    """
    if not matches:
        return
    db = get_admin_client()
    rows = [
        {
            "user_id": user_id,
            "event_id": m["event_id"],
            "match_type": m["match_type"],
            "confidence": m["confidence"],
            "matched_artist_name": m.get("matched_artist_name"),
            "match_reason": m.get("match_reason"),
        }
        for m in matches
    ]
    db.table("user_matches").upsert(rows, on_conflict="user_id,event_id").execute()
    logger.info("Upserted {} matches for user {}", len(rows), user_id)


def get_unnotified_matches(user_id: str) -> list[dict[str, Any]]:
    db = get_admin_client()
    resp = (
        db.table("user_matches")
        .select("*, events(*)")
        .eq("user_id", user_id)
        .is_("notified_at", "null")
        .execute()
    )
    return resp.data or []


def mark_matches_notified(match_ids: list[str]) -> None:
    if not match_ids:
        return
    db = get_admin_client()
    db.table("user_matches").update(
        {"notified_at": datetime.utcnow().isoformat()}
    ).in_("id", match_ids).execute()


# ─────────────────────────────────────────
# Cities
# ─────────────────────────────────────────

def get_active_cities() -> list[dict[str, Any]]:
    db = get_admin_client()
    resp = db.table("cities").select("*").eq("active", True).execute()
    return resp.data or []


def get_city_by_name(name: str) -> dict[str, Any] | None:
    db = get_admin_client()
    resp = db.table("cities").select("*").ilike("name", name).single().execute()
    return resp.data
