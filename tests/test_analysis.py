"""Tests for Gemini analysis context building (no API calls)."""

from __future__ import annotations

from datetime import date, timedelta

from garmin_bia_sync.analysis import (
    DEFAULT_MODEL,
    _coach_error_telegram,
    _is_quota_error,
    _system_prompt,
    analysis_enabled,
    build_analysis_payload,
)
from garmin_bia_sync.report import DayMetrics


def _history(days: int = 14) -> dict[str, DayMetrics]:
    end = date(2026, 6, 7)
    out: dict[str, DayMetrics] = {}
    for i in range(days):
        d = (end - timedelta(days=days - 1 - i)).isoformat()
        out[d] = DayMetrics(67.0 + i * 0.02, 16.0, 31.0, None)
    return out


def test_analysis_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("GEMINI_ANALYSIS_ENABLED", raising=False)
    assert analysis_enabled() is False


def test_build_payload_includes_series(monkeypatch) -> None:
    monkeypatch.setenv("USER_HEIGHT_CM", "176")
    monkeypatch.setenv("ANALYSIS_LOOKBACK_DAYS", "30")
    payload = build_analysis_payload(
        _history(30),
        date(2026, 6, 7),
        "Date: 2026-06-07\n7d avg weight: 67 kg",
    )
    assert payload["report_date"] == "2026-06-07"
    assert len(payload["daily_series"]) == 30
    assert "today_data_digest" in payload
    assert payload["rolling_7d_vs_prior_7d"]["weight_kg_avg"] is not None
    assert payload["daily_series"][-1]["ffm_kg"] is not None


def test_system_prompt_starts_with_verdict_format() -> None:
    prompt = _system_prompt().lower()
    assert "📊 verdict" in prompt
    assert "no greetings" in prompt or "no intro" in prompt
    assert "today_data_digest" in prompt


def test_default_model_is_gemini_35_flash() -> None:
    assert DEFAULT_MODEL == "gemini-3.5-flash"


def test_coach_error_telegram_quota() -> None:
    msg = _coach_error_telegram(Exception("429 quota exceeded"))
    assert "quota" in msg.lower()
    assert _is_quota_error(Exception("RESOURCE_EXHAUSTED"))
