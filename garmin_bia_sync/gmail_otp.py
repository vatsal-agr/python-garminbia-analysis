"""Fetch Garmin MFA OTP from Gmail (for unattended CI login)."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

_GMAIL_QUERY_TEMPLATE = "from:alerts@account.garmin.com subject:\"security passcode\" after:{after_ts}"
OTP_PATTERN = re.compile(r"\b(\d{6})\b")

def gmail_otp_configured() -> bool:
    return bool(os.environ.get("GMAIL_OAUTH_JSON", "").strip())


def _load_gmail_credentials() -> Credentials:
    raw = os.environ.get("GMAIL_OAUTH_JSON")
    if not raw or not raw.strip():
        raise ValueError("GMAIL_OAUTH_JSON is not set")

    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("GMAIL_OAUTH_JSON is not valid JSON") from exc

    if not isinstance(info, dict):
        raise ValueError("GMAIL_OAUTH_JSON must be a JSON object")

    try:
        creds = Credentials.from_authorized_user_info(info)
    except (ValueError, TypeError) as exc:
        raise ValueError(
            "GMAIL_OAUTH_JSON is missing required OAuth user credential fields"
        ) from exc

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    return creds


def _decode_body_data(data: str) -> str:
    if not data:
        return ""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")


def extract_body_text(payload: dict[str, Any]) -> str:
    """Collect plain/html parts from a Gmail message payload."""
    chunks: list[str] = []

    body = payload.get("body") or {}
    if body.get("data"):
        chunks.append(_decode_body_data(body["data"]))

    for part in payload.get("parts") or []:
        mime = (part.get("mimeType") or "").lower()
        part_body = part.get("body") or {}
        if mime in ("text/plain", "text/html") and part_body.get("data"):
            chunks.append(_decode_body_data(part_body["data"]))
        elif part.get("parts"):
            chunks.append(extract_body_text(part))

    return "\n".join(chunks)


def extract_otp_from_text(text: str) -> str | None:
    match = OTP_PATTERN.search(text)
    return match.group(1) if match else None


def _gmail_service():
    from googleapiclient.discovery import build

    creds = _load_gmail_credentials()
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _poll_gmail_for_otp(service: Any, query: str) -> str | None:
    result = (
        service.users()
        .messages()
        .list(userId="me", q=query, maxResults=10)
        .execute()
    )
    for ref in result.get("messages") or []:
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=ref["id"], format="full")
            .execute()
        )
        body = extract_body_text(msg.get("payload") or {})
        otp = extract_otp_from_text(body)
        if otp:
            logger.info("Garmin verification email found in Gmail")
            return otp
    return None


def fetch_garmin_otp_from_gmail(
    wait_seconds: int = 150,
    poll_interval: int = 5,
    after_ts: int | None = None,
) -> str:
    """Poll Gmail for a Garmin verification code. Never logs the OTP."""
    if wait_seconds <= 0:
        raise ValueError("wait_seconds must be positive")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")

    if after_ts is None:
        after_ts = int(time.time()) - 60
    query = _GMAIL_QUERY_TEMPLATE.format(after_ts=after_ts)
    logger.info("Gmail OTP query anchored at ts=%s", after_ts)

    service = _gmail_service()
    max_attempts = max(1, (wait_seconds + poll_interval - 1) // poll_interval)

    for attempt in range(1, max_attempts + 1):
        logger.info(
            "Waiting for Garmin OTP... attempt %s/%s",
            attempt,
            max_attempts,
        )
        otp = _poll_gmail_for_otp(service, query)
        if otp:
            return otp
        if attempt < max_attempts:
            time.sleep(poll_interval)

    raise TimeoutError(
        f"Garmin OTP not found in Gmail within {wait_seconds} seconds"
    )
