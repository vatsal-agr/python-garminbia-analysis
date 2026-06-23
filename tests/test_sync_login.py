"""Tests for Garmin login orchestration."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from garminconnect import GarminConnectAuthenticationError

from garmin_bia_sync import sync


def test_gmail_mode_single_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GMAIL_OAUTH_JSON", '{"token":"x","refresh_token":"r"}')
    monkeypatch.setenv("GARMIN_EMAIL", "a@b.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "secret")
    monkeypatch.delenv("GARMINTOKENS", raising=False)

    login_calls: list[int] = []

    class FakeGarmin:
        def __init__(self, *args, **kwargs):
            self.client = MagicMock()

        def login(self, tokenstore):
            login_calls.append(1)
            return None

    with patch.object(sync, "Garmin", FakeGarmin):
        with patch.object(sync, "begin_otp_session") as begin:
            with patch.object(sync, "delete_garmin_token_files"):
                sync.credential_login_if_needed()

    assert len(login_calls) == 1
    begin.assert_called_once()


def test_mfa_failure_not_retried_without_gmail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GMAIL_OAUTH_JSON", raising=False)
    monkeypatch.setenv("GARMIN_EMAIL", "a@b.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "secret")

    class FakeGarmin:
        def __init__(self, *args, **kwargs):
            pass

        def login(self, tokenstore):
            raise GarminConnectAuthenticationError("Widget MFA failed: bad code")

    with patch.object(sync, "Garmin", FakeGarmin):
        with pytest.raises(GarminConnectAuthenticationError, match="Widget MFA failed"):
            sync.credential_login_if_needed()
