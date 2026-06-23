"""Tests for Gmail OTP fetching (no real API calls)."""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from garmin_bia_sync.gmail_otp import (
    extract_body_text,
    extract_otp_from_text,
    fetch_garmin_otp_from_gmail,
    gmail_otp_configured,
)


def test_gmail_otp_not_configured_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GMAIL_OAUTH_JSON", raising=False)
    assert gmail_otp_configured() is False


def test_extract_otp_from_plain_text() -> None:
    body = "Your Garmin verification code is 482913. It expires in 10 minutes."
    assert extract_otp_from_text(body) == "482913"


def test_extract_otp_from_html() -> None:
    body = "<p>Enter code <b>123456</b> to verify</p>"
    assert extract_otp_from_text(body) == "123456"


def test_extract_body_text_nested_parts() -> None:
    encoded = base64.urlsafe_b64encode(b"Code: 654321").decode()
    payload = {
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": encoded},
            }
        ]
    }
    assert "654321" in extract_body_text(payload)


def test_load_credentials_missing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GMAIL_OAUTH_JSON", raising=False)
    with pytest.raises(ValueError, match="not set"):
        fetch_garmin_otp_from_gmail(wait_seconds=5, poll_interval=5)


def test_load_credentials_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_OAUTH_JSON", "not-json")
    with pytest.raises(ValueError, match="not valid JSON"):
        fetch_garmin_otp_from_gmail(wait_seconds=5, poll_interval=5)


def test_fetch_otp_success_on_second_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = {
        "token": "t",
        "refresh_token": "r",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "sec",
        "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
    }
    monkeypatch.setenv("GMAIL_OAUTH_JSON", json.dumps(oauth))

    encoded = base64.urlsafe_b64encode(
        b"Your verification code is 998877"
    ).decode()
    message = {
        "payload": {
            "mimeType": "text/plain",
            "body": {"data": encoded},
        }
    }

    service = MagicMock()
    list_mock = service.users.return_value.messages.return_value.list
    list_mock.return_value.execute.side_effect = [
        {"messages": []},
        {"messages": [{"id": "msg1"}]},
    ]
    service.users.return_value.messages.return_value.get.return_value.execute.return_value = (
        message
    )

    with patch("garmin_bia_sync.gmail_otp._gmail_service", return_value=service):
        with patch("garmin_bia_sync.gmail_otp.time.sleep"):
            otp = fetch_garmin_otp_from_gmail(wait_seconds=10, poll_interval=5)

    assert otp == "998877"
    assert list_mock.return_value.execute.call_count == 2


def test_fetch_otp_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    oauth = {
        "token": "t",
        "refresh_token": "r",
        "token_uri": "https://oauth2.googleapis.com/token",
        "client_id": "cid",
        "client_secret": "sec",
        "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
    }
    monkeypatch.setenv("GMAIL_OAUTH_JSON", json.dumps(oauth))

    service = MagicMock()
    service.users.return_value.messages.return_value.list.return_value.execute.return_value = (
        {"messages": []}
    )

    with patch("garmin_bia_sync.gmail_otp._gmail_service", return_value=service):
        with patch("garmin_bia_sync.gmail_otp.time.sleep"):
            with pytest.raises(TimeoutError, match="not found"):
                fetch_garmin_otp_from_gmail(wait_seconds=5, poll_interval=5)
