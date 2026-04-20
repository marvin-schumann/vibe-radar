"""Brevo (Sendinblue) integration — transactional email, waitlist contacts, campaigns.

Brevo is the EU-hosted email + contact platform we use for:
- Waitlist signup form submissions (POST → contacts list)
- Transactional emails (signup confirmation, password reset, match alerts)
- Marketing campaigns (launch blast, weekly digests)

API docs: https://developers.brevo.com/reference/getting-started-1
"""

from __future__ import annotations

from typing import Any

import httpx
from loguru import logger

from src.config import settings

BREVO_API_BASE = "https://api.brevo.com/v3"


class BrevoError(Exception):
    """Raised when a Brevo API call fails."""


def _headers() -> dict[str, str]:
    if not settings.brevo_api_key:
        raise BrevoError("BREVO_API_KEY not configured")
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "api-key": settings.brevo_api_key,
    }


# ---------------------------------------------------------------------------
# Contacts (waitlist)
# ---------------------------------------------------------------------------


async def _add_contact_direct(
    email: str,
    attributes: dict[str, Any],
    target_list: int,
) -> dict[str, Any]:
    """Direct contact creation (fallback when DOI template is not configured)."""
    payload = {
        "email": email,
        "attributes": attributes,
        "listIds": [target_list],
        "updateEnabled": True,  # idempotent
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(
                f"{BREVO_API_BASE}/contacts", headers=_headers(), json=payload
            )
        except httpx.HTTPError as e:
            logger.error("brevo direct add failed (network): {}", e)
            raise BrevoError(f"network error: {e}") from e

    if r.status_code in (200, 201, 204):
        logger.info("brevo contact added directly (no DOI): {}", email)
        return {"ok": True, "status": r.status_code, "doi": False}

    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}

    if r.status_code == 400 and body.get("code") == "duplicate_parameter":
        logger.info("brevo contact already existed: {}", email)
        return {"ok": True, "status": 200, "duplicate": True, "doi": False}

    logger.error("brevo direct add failed: {} {}", r.status_code, body)
    raise BrevoError(f"brevo {r.status_code}: {body}")


async def _add_contact_doi(
    email: str,
    attributes: dict[str, Any],
    target_list: int,
    template_id: int,
    redirection_url: str,
) -> dict[str, Any]:
    """Double Opt-In contact creation via Brevo DOI endpoint.

    Brevo sends a confirmation email; contact is only added after the user
    clicks the confirmation link. Required for DSGVO compliance.
    """
    payload: dict[str, Any] = {
        "email": email,
        "includeListIds": [target_list],
        "templateId": template_id,
        "redirectionUrl": redirection_url,
    }
    if attributes:
        payload["attributes"] = attributes

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.post(
                f"{BREVO_API_BASE}/contacts/doubleOptinConfirmation",
                headers=_headers(),
                json=payload,
            )
        except httpx.HTTPError as e:
            logger.error("brevo DOI request failed (network): {}", e)
            raise BrevoError(f"network error: {e}") from e

    if r.status_code in (200, 201, 204):
        logger.info("brevo DOI confirmation email sent to: {}", email)
        return {"ok": True, "status": r.status_code, "doi": True}

    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}

    # Contact already exists and is confirmed — treat as success
    if r.status_code == 400 and body.get("code") == "duplicate_parameter":
        logger.info("brevo DOI contact already existed: {}", email)
        return {"ok": True, "status": 200, "duplicate": True, "doi": True}

    logger.error("brevo DOI request failed: {} {}", r.status_code, body)
    raise BrevoError(f"brevo DOI {r.status_code}: {body}")


async def add_waitlist_contact(
    email: str,
    *,
    first_name: str | None = None,
    city: str | None = None,
    source: str = "landing-page",
    list_id: int | None = None,
) -> dict[str, Any]:
    """Add an email to the waitlist contact list using Double Opt-In (DOI).

    DSGVO-compliant flow:
    1. If BREVO_DOI_TEMPLATE_ID is configured (> 0), use the DOI endpoint.
       Brevo sends a confirmation email; contact is only added after click.
    2. If DOI template is not configured (= 0) or the DOI call fails,
       fall back to direct contact creation with a warning log.

    Idempotent — Brevo updates the existing contact if the email already exists.
    Raises BrevoError on hard failures (4xx/5xx other than 'already exists').
    """
    target_list = list_id or settings.brevo_waitlist_list_id
    attributes: dict[str, Any] = {"SOURCE": source}
    if first_name:
        attributes["FIRSTNAME"] = first_name
    if city:
        attributes["CITY"] = city

    doi_template = settings.brevo_doi_template_id

    # --- Try Double Opt-In first ---
    if doi_template > 0:
        try:
            return await _add_contact_doi(
                email,
                attributes,
                target_list,
                template_id=doi_template,
                redirection_url=settings.brevo_doi_redirection_url,
            )
        except BrevoError:
            logger.warning(
                "brevo DOI failed for {} — falling back to direct add "
                "(check BREVO_DOI_TEMPLATE_ID={})",
                email,
                doi_template,
            )
            # Fall through to direct add
    else:
        logger.warning(
            "brevo DOI not configured (BREVO_DOI_TEMPLATE_ID=0) — "
            "using direct contact add for {}. Set up a DOI template in Brevo "
            "dashboard for full DSGVO compliance.",
            email,
        )

    # --- Fallback: direct add ---
    return await _add_contact_direct(email, attributes, target_list)


# ---------------------------------------------------------------------------
# Transactional email
# ---------------------------------------------------------------------------


async def send_transactional_email(
    *,
    to_email: str,
    to_name: str | None,
    subject: str,
    html_content: str,
    text_content: str | None = None,
    sender_email: str | None = None,
    sender_name: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Send a single transactional email via Brevo SMTP API.

    Used for: signup confirmation, password reset, match alerts, admin
    notifications, weekly digests, etc.
    """
    payload: dict[str, Any] = {
        "sender": {
            "email": sender_email or settings.brevo_sender_email,
            "name": sender_name or settings.brevo_sender_name,
        },
        "to": [{"email": to_email, "name": to_name} if to_name else {"email": to_email}],
        "subject": subject,
        "htmlContent": html_content,
    }
    if text_content:
        payload["textContent"] = text_content
    if tags:
        payload["tags"] = tags

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.post(
                f"{BREVO_API_BASE}/smtp/email", headers=_headers(), json=payload
            )
        except httpx.HTTPError as e:
            logger.error("brevo transactional send failed (network): {}", e)
            raise BrevoError(f"network error: {e}") from e

    if r.status_code in (200, 201):
        body = r.json()
        logger.info(
            "brevo transactional sent: to={} subject={!r} messageId={}",
            to_email,
            subject,
            body.get("messageId"),
        )
        return {"ok": True, "messageId": body.get("messageId")}

    try:
        body = r.json()
    except Exception:
        body = {"raw": r.text}
    logger.error("brevo transactional failed: {} {}", r.status_code, body)
    raise BrevoError(f"brevo {r.status_code}: {body}")


# ---------------------------------------------------------------------------
# Convenience helpers (high-level callers)
# ---------------------------------------------------------------------------


async def notify_admin_signup(*, signup_email: str, approval_url: str) -> None:
    """Send Marvin an email when a new user signs up — replaces the broken
    Telegram notification flagged in the DSGVO audit.
    """
    html = f"""
    <p>New Frequenz signup waiting for approval:</p>
    <p><strong>{signup_email}</strong></p>
    <p><a href="{approval_url}">Approve →</a></p>
    """
    text = f"New Frequenz signup: {signup_email}\n\nApprove: {approval_url}"
    try:
        await send_transactional_email(
            to_email=settings.brevo_admin_notification_email,
            to_name="Frequenz Admin",
            subject=f"New signup: {signup_email}",
            html_content=html,
            text_content=text,
            tags=["admin", "signup"],
        )
    except BrevoError as e:
        logger.warning("admin signup notification failed: {}", e)
        # Don't raise — signup itself should still succeed even if notification fails


async def send_launch_announcement_to(email: str, name: str | None = None) -> None:
    """One-shot launch email for waitlist members. Called from a script
    at launch time, not from the live API."""
    html = """
    <h1>Frequenz is live.</h1>
    <p>Your music taste, mapped to tonight's events.</p>
    <p><a href="https://app.frequenz.live">Open Frequenz →</a></p>
    """
    await send_transactional_email(
        to_email=email,
        to_name=name,
        subject="Frequenz is live — your taste, tonight's events",
        html_content=html,
        tags=["launch", "campaign"],
    )
