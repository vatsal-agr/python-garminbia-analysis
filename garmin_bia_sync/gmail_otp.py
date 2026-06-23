"""Fetch Garmin MFA OTP from Gmail (for unattended CI login)."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

_GMAIL_QUERY_TEMPLATE = (
    'from:alerts@account.garmin.com subject:"security passcode" after:{after_ts}'
)
OTP_PATTERN = re.compile(r"\b(\d{6})\b")
_OTP_CONTEXT_PATTERNS = (
    re.compile(r"one[- ]time[^\d]{0,80}(\d{6})", re.IGNORECASE),
    re.compile(r"passcode[^\d]{0,80}(\d{6})", re.IGNORECASE),
    re.compile(r"security code[^\d]{0,40}(\d{6})", re.IGNORECASE),
    re.compile(r"verification[^\d]{0,80}(\d{6})", re.IGNORECASE),
    re.compile(r"security[^\d]{0,80}(\d{6})", re.IGNORECASE),
    re.compile(r">\s*(\d{6})\s*<"),
    OTP_PATTERN,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


@dataclass
class _OtpSession:
    anchor_ts: int = 0
    mfa_challenge_ts: int = 0
    used_message_ids: set[str] = field(default_factory=set)
    delivered: bool = False


_session = _OtpSession()


def gmail_otp_configured() -> bool:
    return bool(os.environ.get("GMAIL_OAUTH_JSON", "").strip())


def begin_otp_session() -> None:
    """Call once before Garmin login so OTP emails are anchored to this run."""
    _session.anchor_ts = int(time.time()) - 30
    _session.mfa_challenge_ts = 0
    _session.used_message_ids.clear()
    _session.delivered = False
    logger.info("Gmail OTP session started (anchor_ts=%s)", _session.anchor_ts)


def anchor_otp_at_mfa_challenge() -> None:
    """Narrow OTP window to emails sent after Garmin requested MFA."""
    now = int(time.time())
    _session.mfa_challenge_ts = now - 5
    _session.anchor_ts = max(_session.anchor_ts, _session.mfa_challenge_ts)
    _session.delivered = False
    logger.info(
        "Gmail OTP anchored at MFA challenge (mfa_challenge_ts=%s)",
        _session.mfa_challenge_ts,
    )


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


def _collect_parts(
    payload: dict[str, Any], *, mime_filter: set[str] | None = None
) -> list[str]:
    chunks: list[str] = []
    body = payload.get("body") or {}
    mime = (payload.get("mimeType") or "").lower()
    if body.get("data") and (mime_filter is None or mime in mime_filter):
        chunks.append(_decode_body_data(body["data"]))

    for part in payload.get("parts") or []:
        part_mime = (part.get("mimeType") or "").lower()
        part_body = part.get("body") or {}
        if mime_filter is None or part_mime in mime_filter:
            if part_body.get("data"):
                chunks.append(_decode_body_data(part_body["data"]))
        if part.get("parts"):
            chunks.extend(_collect_parts(part, mime_filter=mime_filter))
    return chunks


def extract_body_text(payload: dict[str, Any]) -> str:
    """Collect plain/html parts from a Gmail message payload."""
    plain = _collect_parts(payload, mime_filter={"text/plain"})
    if plain:
        return "\n".join(plain)
    return "\n".join(_collect_parts(payload))


def _normalize_email_text(text: str) -> str:
    without_tags = _HTML_TAG_RE.sub(" ", text)
    return re.sub(r"\s+", " ", without_tags)


def extract_otp_from_text(text: str) -> str | None:
    normalized = _normalize_email_text(text)
    for pattern in _OTP_CONTEXT_PATTERNS:
        match = pattern.search(normalized)
        if match:
            code = match.group(1)
            if code in {"000000", "123456", "111111", "999999"}:
                continue
            return code
    return None


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
    candidates: list[tuple[int, str, str]] = []

    for ref in result.get("messages") or []:
        msg_id = ref["id"]
        if msg_id in _session.used_message_ids:
            continue

        msg = (
            service.users()
            .messages()
            .get(userId="me", id=msg_id, format="full")
            .execute()
        )
        internal_ts = int(msg.get("internalDate", 0)) // 1000
        min_ts = _session.mfa_challenge_ts or _session.anchor_ts
        if internal_ts < min_ts:
            continue

        body = extract_body_text(msg.get("payload") or {})
        otp = extract_otp_from_text(body)
        if otp:
            candidates.append((internal_ts, msg_id, otp))

    if not candidates:
        return None

    internal_ts, msg_id, otp = max(candidates, key=lambda row: row[0])
    _session.used_message_ids.add(msg_id)
    logger.info(
        "Garmin verification email found in Gmail (internal_ts=%s, msg=%s...)",
        internal_ts,
        msg_id[:8],
    )
    return otp


def fetch_garmin_otp_from_gmail(
    wait_seconds: int = 150,
    poll_interval: int = 3,
    after_ts: int | None = None,
) -> str:
    """Poll Gmail for a Garmin verification code. Never logs the OTP."""
    if _session.delivered:
        raise RuntimeError(
            "Garmin OTP already fetched for this login run; refusing a second fetch"
        )

    if wait_seconds <= 0:
        raise ValueError("wait_seconds must be positive")
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")

    if after_ts is not None:
        _session.anchor_ts = after_ts
    elif _session.anchor_ts == 0:
        _session.anchor_ts = int(time.time()) - 30

    query = _GMAIL_QUERY_TEMPLATE.format(after_ts=_session.anchor_ts)
    logger.info("Gmail OTP query anchored at ts=%s", _session.anchor_ts)

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
            _session.delivered = True
            return otp
        if attempt < max_attempts:
            time.sleep(poll_interval)

    raise TimeoutError(
        f"Garmin OTP not found in Gmail within {wait_seconds} seconds"
    )
