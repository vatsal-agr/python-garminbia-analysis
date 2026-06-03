"""Tests for Telegram report formatting."""

from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from garmin_bia_sync.report import (
    DayMetrics,
    _action_line,
    _ffm,
    _ffm_values_in_window,
    _rolling_delta,
    _weight_status_icon,
    format_telegram_report,
    format_telegram_report_decision,
    legacy_report_enabled,
)


def _history() -> dict[str, DayMetrics]:
    """14 days of stable metrics ending 2026-06-07."""
    base = date(2026, 5, 25)
    out: dict[str, DayMetrics] = {}
    for i in range(14):
        d = (base + timedelta(days=i)).isoformat()
        out[d] = DayMetrics(
            weight_kg=67.0 + i * 0.01,
            body_fat_pct=16.0,
            muscle_mass_kg=31.0,
            visceral_fat=None,
        )
    return out


def test_ffm_calculation() -> None:
    assert _ffm(67.42, 16.6) == 56.23


def test_ffm_rolling_average() -> None:
    history = _history()
    end = date(2026, 6, 7)
    values = _ffm_values_in_window(history, end, 7)
    assert len(values) >= 3
    avg, delta = _rolling_delta(history, end, 7, _ffm_values_in_window)
    assert avg is not None
    assert delta is not None


def test_weight_status_icon() -> None:
    assert "✅" in _weight_status_icon(0.15)
    assert "⚠️" in _weight_status_icon(0.05)
    assert "⚠️" in _weight_status_icon(0.25)


def test_action_line_bands() -> None:
    assert _action_line(0.15) == "→ On track"
    assert _action_line(0.05) == "→ Add ~200 kcal/day"
    assert _action_line(0.25) == "→ Reduce slightly"
    assert _action_line(-0.3) == "→ Review intake / recovery"


def test_decision_report_includes_rolling_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_LEGACY_REPORT", raising=False)
    monkeypatch.setenv("USER_HEIGHT_CM", "184")
    msg = format_telegram_report_decision(_history(), date(2026, 6, 7))
    assert "Date: 2026-06-07" in msg
    assert "7d avg weight:" in msg
    assert "7d avg FFM:" in msg
    assert "7d avg FMI:" in msg
    assert "7d muscle/wt:" in msg
    assert "7d visceral" not in msg
    assert "→" in msg
    assert "current weight:" not in msg


def test_legacy_dispatcher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_LEGACY_REPORT", "true")
    assert legacy_report_enabled() is True
    msg = format_telegram_report(_history(), date(2026, 6, 7))
    assert "Sync Status:" in msg
    assert "current weight:" in msg


def test_fmi_missing_height(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("USER_HEIGHT_CM", raising=False)
    msg = format_telegram_report_decision(_history(), date(2026, 6, 7))
    assert "USER_HEIGHT_CM" in msg
