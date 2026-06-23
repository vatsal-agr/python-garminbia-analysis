"""Tests for Gmail-OTP Garmin login patches."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from garminconnect import GarminConnectAuthenticationError

from garmin_bia_sync.garmin_login import login_with_gmail_otp, patch_gmail_otp_mfa


class _FakeResponse:
    def __init__(
        self,
        text: str = "",
        *,
        url: str = "https://sso.garmin.com/signin",
        ok: bool = True,
        status_code: int = 200,
        json_data: dict | None = None,
    ):
        self.text = text
        self.url = url
        self.ok = ok
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self) -> dict:
        return self._json_data


def test_json_mfa_success_before_widget() -> None:
    client = MagicMock()
    client._sso = "https://sso.garmin.com"
    client._portal_service_url = "https://connect.garmin.com/app"
    client._mfa_session = MagicMock()
    client._mfa_session.post.return_value = _FakeResponse(
        json_data={
            "responseStatus": {"type": "SUCCESSFUL"},
            "serviceTicketId": "ST-json-1",
        }
    )

    patch_gmail_otp_mfa(client)
    client._complete_mfa("123456")

    client._establish_session.assert_called_once()
    assert client._establish_session.call_args.args[0] == "ST-json-1"


def test_widget_mfa_refreshes_csrf_before_submit() -> None:
    client = MagicMock()
    client._sso = "https://sso.garmin.com"
    client._mfa_session = MagicMock()
    client._mfa_login_params = {"service": "embed"}
    client._mfa_post_headers = {"Referer": "https://sso.garmin.com"}
    client._widget_last_resp = _FakeResponse(
        '<title>Enter MFA code for login</title>'
        '<input name="_csrf" value="stale-token">'
    )
    client._mfa_flow = "widget"
    client._mfa_session.post.side_effect = [
        _FakeResponse(json_data={"responseStatus": {"type": "FAILED"}}),
        _FakeResponse(json_data={"responseStatus": {"type": "FAILED"}}),
        _FakeResponse(
            '<title>Success</title><a href="embed?ticket=ST-abc123">'
        ),
    ]

    refreshed = _FakeResponse(
        '<title>Enter MFA code for login</title>'
        '<input name="_csrf" value="fresh-token">'
    )
    client._mfa_session.get.return_value = refreshed

    patch_gmail_otp_mfa(client)
    client._complete_mfa("123456")

    client._mfa_session.get.assert_called_once()
    widget_post = client._mfa_session.post.call_args_list[-1].kwargs["data"]
    assert widget_post["_csrf"] == "fresh-token"
    assert widget_post["mfa-code"] == "123456"


def test_login_with_gmail_otp_uses_widget_first() -> None:
    from garminconnect.client import _MFARequired as RealMfa

    client = MagicMock()
    client._widget_web_login.side_effect = RealMfa()
    callback = MagicMock()

    login_with_gmail_otp(
        client,
        "a@b.com",
        "secret",
        lambda: "654321",
        on_mfa_required=callback,
    )

    client._widget_web_login.assert_called_once()
    client._portal_web_login_requests.assert_not_called()
    callback.assert_called_once()


def test_login_with_gmail_otp_falls_back_to_portal() -> None:
    from garminconnect.client import _MFARequired as RealMfa

    client = MagicMock()
    client._widget_web_login.side_effect = Exception("widget down")
    client._portal_web_login_requests.side_effect = RealMfa()

    login_with_gmail_otp(client, "a@b.com", "secret", lambda: "111222")

    client._portal_web_login_requests.assert_called_once()


def test_widget_mfa_tries_alternate_from_page() -> None:
    client = MagicMock()
    client._sso = "https://sso.garmin.com"
    client._mfa_session = MagicMock()
    client._mfa_login_params = {}
    client._mfa_post_headers = {}
    client._mfa_flow = "widget"
    client._widget_last_resp = _FakeResponse(
        '<title>Enter MFA code for login</title>'
        '<input name="_csrf" value="token-a">'
    )

    still_mfa = _FakeResponse(
        '<title>Enter MFA code for login</title>'
        '<input name="_csrf" value="token-b">'
    )
    success = _FakeResponse(
        '<title>Success</title><a href="embed?ticket=ST-ticket-xyz">'
    )
    client._mfa_session.get.return_value = client._widget_last_resp
    client._mfa_session.post.side_effect = [
        _FakeResponse(json_data={"responseStatus": {"type": "FAILED"}}),
        _FakeResponse(json_data={"responseStatus": {"type": "FAILED"}}),
        still_mfa,
        success,
    ]

    patch_gmail_otp_mfa(client)
    client._complete_mfa("999888")

    widget_posts = [
        call
        for call in client._mfa_session.post.call_args_list
        if "verifyMFA" in str(call.args[0])
    ]
    assert len(widget_posts) == 2
    assert widget_posts[0].kwargs["data"]["fromPage"] == "enterMfaCode"
    assert widget_posts[1].kwargs["data"]["fromPage"] == "setupEnterMfaCode"


def test_widget_mfa_raises_on_persistent_failure() -> None:
    client = MagicMock()
    client._sso = "https://sso.garmin.com"
    client._mfa_session = MagicMock()
    client._mfa_login_params = {}
    client._mfa_post_headers = {}
    client._mfa_flow = "widget"
    client._widget_last_resp = _FakeResponse(
        '<title>Enter MFA code for login</title>'
        '<input name="_csrf" value="token-a">'
    )
    bad = _FakeResponse('<title>Enter MFA code for login</title>')
    client._mfa_session.get.return_value = client._widget_last_resp
    client._mfa_session.post.side_effect = [
        _FakeResponse(json_data={"responseStatus": {"type": "FAILED"}}),
        _FakeResponse(json_data={"responseStatus": {"type": "FAILED"}}),
        bad,
        bad,
    ]

    patch_gmail_otp_mfa(client)
    with pytest.raises(GarminConnectAuthenticationError, match="Widget MFA failed"):
        client._complete_mfa("000000")
