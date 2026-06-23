"""Garmin login helpers tuned for unattended Gmail OTP (CI / local automation)."""

from __future__ import annotations

import logging
import re
from typing import Any, Callable

from garminconnect.client import (
    IOS_SERVICE_URL,
    IOS_SSO_CLIENT_ID,
    PORTAL_SSO_CLIENT_ID,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
    _MFARequired,
    _CSRF_RE,
    _TITLE_RE,
    _random_browser_headers,
)

logger = logging.getLogger(__name__)

_MFA_TITLE_HINTS = (
    "mfa",
    "authentication application",
    "security passcode",
    "verification code",
)

_WIDGET_FROM_PAGES = ("enterMfaCode", "setupEnterMfaCode")
_TICKET_RE = re.compile(r'\?ticket=(ST-[^"&\s]+)')
_HIDDEN_INPUT_RE = re.compile(
    r'<input[^>]*\bname="([^"]+)"[^>]*\bvalue="([^"]*)"',
    re.IGNORECASE,
)


def _widget_mfa_page_needs_code(title: str) -> bool:
    lowered = title.lower()
    return any(hint in lowered for hint in _MFA_TITLE_HINTS)


def _parse_widget_form_fields(html: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for match in _HIDDEN_INPUT_RE.finditer(html):
        name = match.group(1)
        if name and name not in fields:
            fields[name] = match.group(2)
    return fields


def _try_json_mfa_endpoints(client: Any, mfa_code: str) -> bool:
    """Try portal/mobile JSON verify endpoints (no CSRF; works with widget cookies)."""
    sess = getattr(client, "_mfa_session", None)
    if not sess:
        return False

    mfa_method = getattr(client, "_mfa_method", "email")
    mfa_json: dict[str, Any] = {
        "mfaMethod": mfa_method,
        "mfaVerificationCode": mfa_code,
        "rememberMyBrowser": True,
        "reconsentList": [],
        "mfaSetup": False,
    }
    browser_hdrs = _random_browser_headers()
    post_headers = {
        **browser_hdrs,
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Content-Type": "application/json",
        "Origin": client._sso,
    }
    portal_params = {
        "clientId": PORTAL_SSO_CLIENT_ID,
        "locale": "en-US",
        "service": client._portal_service_url,
    }
    mobile_params = {
        "clientId": IOS_SSO_CLIENT_ID,
        "locale": "en-US",
        "service": IOS_SERVICE_URL,
    }
    endpoints: list[tuple[str, dict[str, str], str]] = [
        (f"{client._sso}/portal/api/mfa/verifyCode", portal_params, "portal"),
        (f"{client._sso}/mobile/api/mfa/verifyCode", mobile_params, "mobile"),
    ]

    for mfa_url, params, label in endpoints:
        headers = {
            **post_headers,
            "Referer": (
                f"{client._sso}/portal/sso/en-US/sign-in"
                f"?clientId={PORTAL_SSO_CLIENT_ID}"
                f"&service={client._portal_service_url}"
            ),
        }
        try:
            logger.info("Trying Garmin JSON MFA verify (%s)", label)
            response = sess.post(
                mfa_url,
                params=params,
                headers=headers,
                json=mfa_json,
                timeout=30,
            )
        except Exception as exc:
            logger.warning("JSON MFA %s connection error: %s", label, exc)
            continue

        if response.status_code == 429:
            logger.warning("JSON MFA %s returned 429", label)
            continue

        try:
            result = response.json()
        except Exception:
            logger.warning(
                "JSON MFA %s returned non-JSON (HTTP %s)",
                label,
                response.status_code,
            )
            continue

        if result.get("error", {}).get("status-code") == "429":
            logger.warning("JSON MFA %s returned 429 in JSON body", label)
            continue

        status = result.get("responseStatus", {}).get("type")
        if status == "SUCCESSFUL":
            ticket = result["serviceTicketId"]
            service_url = (
                IOS_SERVICE_URL
                if label == "mobile"
                else getattr(client, "_mfa_service_url", client._portal_service_url)
            )
            logger.info("Garmin JSON MFA verify succeeded (%s)", label)
            client._establish_session(ticket, sess=sess, service_url=service_url)
            return True

        logger.warning("JSON MFA %s failed: %s", label, result)

    return False


def patch_gmail_otp_mfa(client: Any) -> None:
    """Patch MFA completion: JSON verify first, then hardened widget HTML."""
    if getattr(client, "_gmail_otp_mfa_patched", None) is True:
        return

    original_complete_mfa = client._complete_mfa

    def _complete_mfa_widget(mfa_code: str) -> None:
        sess = getattr(client, "_mfa_session", None)
        last_resp = getattr(client, "_widget_last_resp", None)
        if not sess or not last_resp:
            raise GarminConnectAuthenticationError("Missing widget MFA context")

        params = getattr(client, "_mfa_login_params", {})
        headers = getattr(client, "_mfa_post_headers", {})
        page_html = last_resp.text

        refresh = sess.get(
            last_resp.url,
            params=params,
            headers=headers,
            timeout=30,
        )
        if refresh.ok:
            page_html = refresh.text
            client._widget_last_resp = refresh
        else:
            logger.warning(
                "Widget MFA page refresh returned HTTP %s; using cached page",
                refresh.status_code,
            )

        hidden_fields = _parse_widget_form_fields(page_html)
        csrf_match = _CSRF_RE.search(page_html)
        csrf = hidden_fields.get("_csrf") or (csrf_match.group(1) if csrf_match else "")
        if not csrf:
            raise GarminConnectAuthenticationError("Widget MFA: missing CSRF token")

        from_page = hidden_fields.get("fromPage")
        from_pages = (from_page,) if from_page else _WIDGET_FROM_PAGES

        last_title = ""
        for page_name in from_pages:
            post_data = {
                **{k: v for k, v in hidden_fields.items() if k not in ("mfa-code",)},
                "mfa-code": mfa_code,
                "embed": hidden_fields.get("embed", "true"),
                "_csrf": csrf,
                "fromPage": page_name,
            }
            post_resp = sess.post(
                f"{client._sso}/sso/verifyMFA/loginEnterMfaCode",
                params=params,
                headers=headers,
                data=post_data,
                timeout=30,
            )

            if post_resp.status_code == 429:
                raise GarminConnectTooManyRequestsError(
                    "Widget MFA verify returned 429"
                )

            title_match = _TITLE_RE.search(post_resp.text)
            title = title_match.group(1) if title_match else ""
            last_title = title

            if title == "Success":
                ticket_match = _TICKET_RE.search(post_resp.text)
                if not ticket_match:
                    raise GarminConnectAuthenticationError(
                        "Widget MFA: missing service ticket"
                    )
                client._establish_session(
                    ticket_match.group(1),
                    sess=sess,
                    service_url=f"{client._sso}/sso/embed",
                )
                return

            if _widget_mfa_page_needs_code(title):
                hidden_fields = _parse_widget_form_fields(post_resp.text)
                csrf_match = _CSRF_RE.search(post_resp.text)
                csrf = hidden_fields.get("_csrf") or (
                    csrf_match.group(1) if csrf_match else csrf
                )
                page_html = post_resp.text
                continue

            break

        raise GarminConnectAuthenticationError(f"Widget MFA failed: {last_title}")

    def _complete_mfa(mfa_code: str) -> None:
        if _try_json_mfa_endpoints(client, mfa_code):
            return

        flow = getattr(client, "_mfa_flow", "portal")
        if flow == "widget":
            _complete_mfa_widget(mfa_code)
            return

        original_complete_mfa(mfa_code)

    client._complete_mfa = _complete_mfa  # type: ignore[method-assign]
    client._complete_mfa_widget = _complete_mfa_widget  # type: ignore[method-assign]
    client._gmail_otp_mfa_patched = True


# Backward-compatible alias used by sync.py
def patch_widget_mfa_completion(client: Any) -> None:
    patch_gmail_otp_mfa(client)


def login_with_gmail_otp(
    client: Any,
    email: str,
    password: str,
    prompt_mfa: Callable[[], str],
    *,
    on_mfa_required: Callable[[], None] | None = None,
) -> None:
    """Widget-first login; portal skipped (often 403 + long WAF delays on automation)."""
    patch_gmail_otp_mfa(client)

    strategies: list[tuple[str, Any]] = [
        ("widget+cffi", lambda: client._widget_web_login(email, password)),
        ("portal+requests", lambda: client._portal_web_login_requests(email, password)),
    ]

    last_err: Exception | None = None
    rate_limited_count = 0

    for name, run in strategies:
        try:
            logger.info("Trying Garmin login strategy: %s", name)
            run()
            return
        except GarminConnectAuthenticationError:
            raise
        except _MFARequired:
            if on_mfa_required:
                on_mfa_required()
            mfa_code = prompt_mfa()
            client._complete_mfa(mfa_code)
            return
        except GarminConnectTooManyRequestsError as exc:
            logger.warning("%s returned 429: %s", name, exc)
            rate_limited_count += 1
            last_err = exc
            continue
        except Exception as exc:
            logger.warning("%s failed: %s", name, exc)
            last_err = exc
            continue

    if rate_limited_count == len(strategies):
        raise GarminConnectTooManyRequestsError(
            "All login strategies rate limited (429). "
            "Try again later or check your IP/network."
        )
    raise GarminConnectConnectionError(
        f"All login strategies exhausted: {last_err}"
    )
