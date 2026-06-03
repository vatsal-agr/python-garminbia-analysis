"""Telegram report formatting: legacy digest vs decision-oriented rolling metrics."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

import gspread

logger = logging.getLogger(__name__)

ROLLING_DAYS = 7
MIN_DAYS_IN_WINDOW = 3
DEFAULT_WEIGHT_TARGET_MIN_KG = 0.10
DEFAULT_WEIGHT_TARGET_MAX_KG = 0.20


@dataclass(frozen=True)
class DayMetrics:
    weight_kg: float | None
    body_fat_pct: float | None
    muscle_mass_kg: float | None
    visceral_fat: float | None = None


def parse_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def metrics_from_row(row: list[Any]) -> DayMetrics:
    return DayMetrics(
        weight_kg=parse_number(row[1] if len(row) > 1 else None),
        body_fat_pct=parse_number(row[3] if len(row) > 3 else None),
        muscle_mass_kg=parse_number(row[6] if len(row) > 6 else None),
        visceral_fat=parse_number(row[7] if len(row) > 7 else None),
    )


def load_sheet_history(worksheet: gspread.Worksheet) -> dict[str, DayMetrics]:
    """Build date → metrics from the sheet (after sync, so includes new rows)."""
    values = worksheet.get_all_values()
    if not values:
        return {}

    headers = [str(cell).strip().lower() for cell in values[0]]
    try:
        date_col = headers.index("date")
        weight_col = headers.index("weight_kg")
        bf_col = headers.index("body_fat_pct")
        muscle_col = headers.index("muscle_mass_kg")
    except ValueError as exc:
        raise ValueError(
            "Sheet header row must include date, weight_kg, body_fat_pct, muscle_mass_kg. "
            f"Found: {headers}"
        ) from exc

    visceral_col = headers.index("visceral_fat") if "visceral_fat" in headers else None

    history: dict[str, DayMetrics] = {}
    for row in values[1:]:
        if date_col >= len(row):
            continue
        day = str(row[date_col]).strip()
        if not day:
            continue
        history[day] = DayMetrics(
            weight_kg=parse_number(row[weight_col] if weight_col < len(row) else None),
            body_fat_pct=parse_number(row[bf_col] if bf_col < len(row) else None),
            muscle_mass_kg=parse_number(row[muscle_col] if muscle_col < len(row) else None),
            visceral_fat=(
                parse_number(row[visceral_col] if visceral_col is not None and visceral_col < len(row) else None)
            ),
        )
    return history


def merge_synced_rows(
    history: dict[str, DayMetrics],
    synced_rows: dict[str, list[Any]],
) -> dict[str, DayMetrics]:
    """Prefer in-memory rows from this run (avoids stale sheet reads)."""
    merged = dict(history)
    for day, row in synced_rows.items():
        merged[day] = metrics_from_row(row)
    return merged


def today_local() -> date:
    tz_name = os.getenv("SYNC_TIMEZONE", "Asia/Kolkata")
    return datetime.now(ZoneInfo(tz_name)).date()


def legacy_report_enabled() -> bool:
    return os.getenv("TELEGRAM_LEGACY_REPORT", "false").lower() in ("true", "1", "yes")


def _weight_target_band() -> tuple[float, float]:
    low = parse_number(os.getenv("WEIGHT_TARGET_MIN_KG"))
    high = parse_number(os.getenv("WEIGHT_TARGET_MAX_KG"))
    return (
        low if low is not None else DEFAULT_WEIGHT_TARGET_MIN_KG,
        high if high is not None else DEFAULT_WEIGHT_TARGET_MAX_KG,
    )


def _user_height_cm() -> float | None:
    return parse_number(os.getenv("USER_HEIGHT_CM"))


def _mean_any(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _mean_window(values: list[float]) -> float | None:
    if len(values) < MIN_DAYS_IN_WINDOW:
        return None
    return round(sum(values) / len(values), 2)


def _ffm(weight_kg: float | None, body_fat_pct: float | None) -> float | None:
    if weight_kg is None or body_fat_pct is None:
        return None
    return round(weight_kg * (1 - body_fat_pct / 100), 2)


def _fmi(weight_kg: float | None, body_fat_pct: float | None, height_cm: float) -> float | None:
    if weight_kg is None or body_fat_pct is None:
        return None
    height_m = height_cm / 100
    if height_m <= 0:
        return None
    fat_mass = weight_kg * body_fat_pct / 100
    return round(fat_mass / (height_m**2), 2)


def _muscle_weight_pct(metrics: DayMetrics) -> float | None:
    if metrics.weight_kg is None or metrics.muscle_mass_kg is None or metrics.weight_kg <= 0:
        return None
    return round(100 * metrics.muscle_mass_kg / metrics.weight_kg, 1)


def _format_delta(current: float | None, previous: float | None, unit: str) -> str:
    if current is None or previous is None:
        return "(n/a)"
    diff = round(current - previous, 2)
    if diff > 0:
        return f"(+{diff}{unit})"
    if diff < 0:
        return f"({diff}{unit})"
    return f"(+0{unit})"


def _format_week_delta(delta: float | None, unit: str) -> str:
    if delta is None:
        return "(n/a)"
    if delta > 0:
        return f"(+{delta}{unit})"
    if delta < 0:
        return f"({delta}{unit})"
    return f"(+0{unit})"


def _format_delta_pp(current: float | None, previous: float | None) -> str:
    if current is None or previous is None:
        return "(n/a)"
    diff = round(current - previous, 1)
    if diff > 0:
        return f"(+{diff} pp)"
    if diff < 0:
        return f"({diff} pp)"
    return "(+0 pp)"


def _values_in_window(
    history: dict[str, DayMetrics],
    end: date,
    days: int,
    field: str,
) -> list[float]:
    values: list[float] = []
    for offset in range(days - 1, -1, -1):
        key = (end - timedelta(days=offset)).isoformat()
        metrics = history.get(key)
        if not metrics:
            continue
        value = getattr(metrics, field)
        if value is not None:
            values.append(float(value))
    return values


def _ffm_values_in_window(
    history: dict[str, DayMetrics],
    end: date,
    days: int,
) -> list[float]:
    values: list[float] = []
    for offset in range(days - 1, -1, -1):
        key = (end - timedelta(days=offset)).isoformat()
        metrics = history.get(key)
        if not metrics:
            continue
        ffm = _ffm(metrics.weight_kg, metrics.body_fat_pct)
        if ffm is not None:
            values.append(ffm)
    return values


def _fmi_values_in_window(
    history: dict[str, DayMetrics],
    end: date,
    days: int,
    height_cm: float,
) -> list[float]:
    values: list[float] = []
    for offset in range(days - 1, -1, -1):
        key = (end - timedelta(days=offset)).isoformat()
        metrics = history.get(key)
        if not metrics:
            continue
        fmi = _fmi(metrics.weight_kg, metrics.body_fat_pct, height_cm)
        if fmi is not None:
            values.append(fmi)
    return values


def _muscle_ratio_values_in_window(
    history: dict[str, DayMetrics],
    end: date,
    days: int,
) -> list[float]:
    values: list[float] = []
    for offset in range(days - 1, -1, -1):
        key = (end - timedelta(days=offset)).isoformat()
        metrics = history.get(key)
        if not metrics:
            continue
        ratio = _muscle_weight_pct(metrics)
        if ratio is not None:
            values.append(ratio)
    return values


def _rolling_delta(
    history: dict[str, DayMetrics],
    end: date,
    days: int,
    collector,
) -> tuple[float | None, float | None]:
    """Return (current_window_avg, delta vs prior window) from a collector callable."""
    this_vals = collector(history, end, days)
    prior_end = end - timedelta(days=days)
    prior_vals = collector(history, prior_end, days)
    current_avg = _mean_window(this_vals)
    prior_avg = _mean_window(prior_vals)
    if current_avg is None or prior_avg is None:
        return current_avg, None
    return current_avg, round(current_avg - prior_avg, 2)


def _visceral_data_available(history: dict[str, DayMetrics], report_date: date) -> bool:
    this_vals = _values_in_window(history, report_date, ROLLING_DAYS, "visceral_fat")
    prior_end = report_date - timedelta(days=ROLLING_DAYS)
    prior_vals = _values_in_window(history, prior_end, ROLLING_DAYS, "visceral_fat")
    return len(this_vals) >= MIN_DAYS_IN_WINDOW or len(prior_vals) >= MIN_DAYS_IN_WINDOW


def _weight_status_icon(delta: float | None) -> str:
    if delta is None:
        return ""
    low, high = _weight_target_band()
    if low <= delta <= high:
        return " ✅"
    return " ⚠️"


def _action_line(weight_delta: float | None) -> str:
    if weight_delta is None:
        return "→ Insufficient history for guidance"
    low, high = _weight_target_band()
    if weight_delta < 0:
        return "→ Review intake / recovery"
    if low <= weight_delta <= high:
        return "→ On track"
    if weight_delta < low:
        return "→ Add ~200 kcal/day"
    return "→ Reduce slightly"


NO_WEIGH_IN_TODAY_MESSAGE = (
    "No weigh-in today.\n\n"
    "No body-composition data was synced in this run's lookback window."
)


def has_weigh_in(metrics: DayMetrics | None) -> bool:
    return metrics is not None and metrics.weight_kg is not None


def today_has_weigh_in(history: dict[str, DayMetrics]) -> bool:
    return has_weigh_in(history.get(today_local().isoformat()))


def latest_weigh_in_date(
    history: dict[str, DayMetrics],
    dates: list[str],
) -> date | None:
    """Most recent date in *dates* that has weight on the sheet."""
    candidates = [d for d in dates if has_weigh_in(history.get(d))]
    if not candidates:
        return None
    return max(date.fromisoformat(d) for d in candidates)


def pick_report_date(synced_dates: list[str], history: dict[str, DayMetrics]) -> date:
    """Report anchor: today if weighed in today, else latest day with weight in *synced_dates*."""
    today = today_local()
    if today_has_weigh_in(history):
        return today
    latest = latest_weigh_in_date(history, synced_dates)
    if latest is not None:
        return latest
    return today


@dataclass(frozen=True)
class DailyTelegramPlan:
    """What to send after a scheduled (non-SYNC_DATE) sync run."""

    send_telegram: bool
    message: str | None
    report_date: date | None
    run_gemini: bool


def format_daily_telegram(
    history: dict[str, DayMetrics],
    report_date: date,
    *,
    scope: Literal["today", "stale"] = "today",
    status: str = "OK",
) -> str:
    body = format_telegram_report(history, report_date, status=status)
    if scope == "today":
        return body
    today_key = today_local().isoformat()
    return (
        f"No weigh-in today ({today_key}).\n\n"
        f"Latest data — {report_date.isoformat()}:\n\n"
        f"{body}"
    )


def plan_daily_telegram(
    history: dict[str, DayMetrics],
    synced_dates: list[str],
) -> DailyTelegramPlan:
    """Decide digest vs no-data message; Gemini only when today has a weigh-in."""
    today = today_local()

    if today_has_weigh_in(history):
        return DailyTelegramPlan(
            send_telegram=True,
            message=format_daily_telegram(history, today, scope="today"),
            report_date=today,
            run_gemini=True,
        )

    latest = latest_weigh_in_date(history, synced_dates)
    if latest is not None:
        return DailyTelegramPlan(
            send_telegram=True,
            message=format_daily_telegram(history, latest, scope="stale"),
            report_date=latest,
            run_gemini=False,
        )

    return DailyTelegramPlan(
        send_telegram=True,
        message=NO_WEIGH_IN_TODAY_MESSAGE,
        report_date=None,
        run_gemini=False,
    )


def format_telegram_report(
    history: dict[str, DayMetrics],
    report_date: date,
    *,
    status: str = "OK",
) -> str:
    if legacy_report_enabled():
        return format_telegram_report_legacy(history, report_date, status=status)
    return format_telegram_report_decision(history, report_date)


def format_telegram_report_legacy(
    history: dict[str, DayMetrics],
    report_date: date,
    *,
    status: str = "OK",
) -> str:
    key = report_date.isoformat()
    current = history.get(key)
    if not current:
        return (
            f'Sync Status: "{status}"\n'
            f'Date: "{key}"\n\n'
            "No metrics on sheet for this date."
        )

    cur_weight = current.weight_kg
    cur_bf = current.body_fat_pct
    cur_muscle = current.muscle_mass_kg
    cur_ffm = _ffm(cur_weight, cur_bf)

    this_window = _values_in_window(history, report_date, ROLLING_DAYS, "weight_kg")
    prior_end = report_date - timedelta(days=ROLLING_DAYS)
    prior_window = _values_in_window(history, prior_end, ROLLING_DAYS, "weight_kg")

    rolling_avg = _mean_any(this_window)
    prior_rolling_avg = _mean_any(prior_window)
    rolling_delta = (
        round(rolling_avg - prior_rolling_avg, 2)
        if rolling_avg is not None and prior_rolling_avg is not None
        else None
    )

    week_ago_key = (report_date - timedelta(days=ROLLING_DAYS)).isoformat()
    week_ago = history.get(week_ago_key)
    week_ago_ffm = _ffm(
        week_ago.weight_kg if week_ago else None,
        week_ago.body_fat_pct if week_ago else None,
    )

    weight_line = (
        f"{cur_weight} kg {_format_delta(cur_weight, week_ago.weight_kg if week_ago else None, ' kg')}"
        if cur_weight is not None
        else "n/a"
    )
    bf_line = (
        f"{cur_bf}% {_format_delta(cur_bf, week_ago.body_fat_pct if week_ago else None, '%')}"
        if cur_bf is not None
        else "n/a"
    )
    muscle_line = (
        f"{cur_muscle} kg "
        f"{_format_delta(cur_muscle, week_ago.muscle_mass_kg if week_ago else None, ' kg')}"
        if cur_muscle is not None
        else "n/a"
    )
    ffm_line = (
        f"{cur_ffm} kg {_format_delta(cur_ffm, week_ago_ffm, ' kg')}"
        if cur_ffm is not None
        else "n/a"
    )

    rolling_avg_text = f"{rolling_avg} kg" if rolling_avg is not None else "n/a"
    if rolling_delta is not None:
        sign = "+" if rolling_delta > 0 else ""
        rolling_delta_text = f"{sign}{rolling_delta} kg"
    else:
        rolling_delta_text = "n/a"

    return (
        f'Sync Status: "{status}"\n'
        f'Date: "{key}"\n\n'
        f"7 day rolling avg weight: {rolling_avg_text}\n"
        f"rolling avg delta: {rolling_delta_text}\n\n"
        f"current weight: {weight_line}\n"
        f"current bf: {bf_line}\n"
        f"current muscle: {muscle_line}\n"
        f"current FFM: {ffm_line}"
    )


def format_telegram_report_decision(
    history: dict[str, DayMetrics],
    report_date: date,
) -> str:
    key = report_date.isoformat()
    lines = [f"Date: {key}", ""]

    weight_collector = lambda h, e, d: _values_in_window(h, e, d, "weight_kg")
    weight_avg, weight_delta = _rolling_delta(
        history, report_date, ROLLING_DAYS, weight_collector
    )
    if weight_avg is not None:
        lines.append(
            f"7d avg weight: {weight_avg} kg {_format_week_delta(weight_delta, ' kg')}"
            f"{_weight_status_icon(weight_delta)}"
        )
    else:
        lines.append("7d avg weight: n/a")

    ffm_avg, ffm_delta = _rolling_delta(history, report_date, ROLLING_DAYS, _ffm_values_in_window)
    if ffm_avg is not None:
        lines.append(f"7d avg FFM:    {ffm_avg} kg {_format_week_delta(ffm_delta, ' kg')}")
    else:
        lines.append("7d avg FFM:    n/a")

    height_cm = _user_height_cm()
    if height_cm is None:
        lines.append("7d avg FMI:    n/a (set USER_HEIGHT_CM)")
    else:
        fmi_collector = lambda h, e, d: _fmi_values_in_window(h, e, d, height_cm)
        fmi_avg, fmi_delta = _rolling_delta(
            history, report_date, ROLLING_DAYS, fmi_collector
        )
        if fmi_avg is not None:
            lines.append(
                f"7d avg FMI:    {fmi_avg} kg/m² {_format_week_delta(fmi_delta, '')}"
            )
        else:
            lines.append("7d avg FMI:    n/a")

    muscle_avg, muscle_delta = _rolling_delta(
        history, report_date, ROLLING_DAYS, _muscle_ratio_values_in_window
    )
    if muscle_avg is not None:
        prior = muscle_avg - muscle_delta if muscle_delta is not None else None
        lines.append(
            f"7d muscle/wt:  {muscle_avg}% {_format_delta_pp(muscle_avg, prior)}"
        )
    else:
        lines.append("7d muscle/wt:  n/a")

    if _visceral_data_available(history, report_date):
        visceral_collector = lambda h, e, d: _values_in_window(h, e, d, "visceral_fat")
        visceral_avg, visceral_delta = _rolling_delta(
            history, report_date, ROLLING_DAYS, visceral_collector
        )
        if visceral_avg is not None:
            lines.append(
                f"7d visceral:   {visceral_avg} {_format_week_delta(visceral_delta, '')}"
            )
        else:
            lines.append("7d visceral:   n/a")

    lines.append("")
    lines.append(_action_line(weight_delta))
    return "\n".join(lines)
