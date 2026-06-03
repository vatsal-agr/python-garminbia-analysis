#!/usr/bin/env python3
"""Sync Garmin body-composition (BIA) data to Google Sheets and notify via Telegram."""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from getpass import getpass
from typing import Any
from zoneinfo import ZoneInfo

import gspread
import requests
from google.oauth2.service_account import Credentials
from garminconnect import (
    Garmin,
    GarminConnectAuthenticationError,
    GarminConnectConnectionError,
    GarminConnectTooManyRequestsError,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SHEET_HEADERS = [
    "date",
    "weight_kg",
    "bmi",
    "body_fat_pct",
    "body_water_pct",
    "bone_mass_kg",
    "muscle_mass_kg",
    "visceral_fat",
    "metabolic_age",
    "source",
    "synced_at_utc",
]

GRAMS_THRESHOLD = 1000
ROLLING_DAYS = 7


@dataclass(frozen=True)
class DayMetrics:
    weight_kg: float | None
    body_fat_pct: float | None
    muscle_mass_kg: float | None


def _parse_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _grams_to_kg(value: float | int | None) -> float | None:
    if value is None:
        return None
    number = float(value)
    if number >= GRAMS_THRESHOLD:
        return round(number / 1000, 2)
    return round(number, 2)


def _grams_field_to_kg(value: float | int | None) -> float | None:
    """Convert mass fields that Garmin returns in grams."""
    if value is None:
        return None
    number = float(value)
    if number >= GRAMS_THRESHOLD:
        return round(number / 1000, 2)
    return round(number, 2)


def send_telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("Telegram not configured; skipping notification")
        return
    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=30,
    )
    response.raise_for_status()


def init_garmin() -> Garmin:
    tokenstore = os.getenv("GARMINTOKENS", "~/.garminconnect")
    email = os.getenv("GARMIN_EMAIL")
    password = os.getenv("GARMIN_PASSWORD")
    mfa_code = os.getenv("GARMIN_MFA")

    def prompt_mfa() -> str:
        if mfa_code:
            return mfa_code.strip()
        if sys.stdin.isatty():
            return input("Garmin MFA code: ").strip()
        raise GarminConnectAuthenticationError(
            "MFA required but no interactive terminal. Set GARMIN_MFA or GARMINTOKENS."
        )

    try:
        if email and password:
            garmin = Garmin(email=email, password=password, prompt_mfa=prompt_mfa)
        else:
            garmin = Garmin()
        garmin.login(tokenstore)
        return garmin
    except GarminConnectTooManyRequestsError:
        raise
    except (GarminConnectAuthenticationError, GarminConnectConnectionError) as exc:
        if not email or not password:
            raise GarminConnectAuthenticationError(
                "Garmin login failed. Run locally once with GARMIN_EMAIL/PASSWORD "
                "to create tokens, then set GARMINTOKENS for GitHub Actions."
            ) from exc
        raise


def credential_login_if_needed() -> Garmin:
    """Interactive login when tokens are missing (first local run)."""
    tokenstore = os.getenv("GARMINTOKENS", "~/.garminconnect")
    try:
        return init_garmin()
    except GarminConnectAuthenticationError:
        pass

    email = os.getenv("GARMIN_EMAIL") or input("Garmin email: ").strip()
    password = os.getenv("GARMIN_PASSWORD") or getpass("Garmin password: ")

    garmin = Garmin(
        email=email,
        password=password,
        prompt_mfa=lambda: (
            os.getenv("GARMIN_MFA") or input("Garmin MFA code: ").strip()
        ),
    )
    garmin.login(tokenstore)
    logger.info("Garmin login OK. Tokens saved under %s", tokenstore)
    return garmin


def pick_sample(body: dict[str, Any]) -> dict[str, Any] | None:
    entries = body.get("dateWeightList") or []
    if entries:
        return max(
            entries,
            key=lambda row: row.get("timestampGMT") or row.get("date") or 0,
        )
    average = body.get("totalAverage") or {}
    if average.get("weight") is not None:
        return average
    return None


def body_to_row(sync_date: str, sample: dict[str, Any], source: str) -> list[Any]:
    synced_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return [
        sync_date,
        _grams_to_kg(sample.get("weight")),
        sample.get("bmi"),
        sample.get("bodyFat"),
        sample.get("bodyWater"),
        _grams_field_to_kg(sample.get("boneMass")),
        _grams_field_to_kg(sample.get("muscleMass")),
        sample.get("visceralFat"),
        sample.get("metabolicAge"),
        source,
        synced_at,
    ]


def fetch_bia_row(garmin: Garmin, sync_date: str) -> list[Any] | None:
    body = garmin.get_body_composition(sync_date)
    sample = pick_sample(body)
    if not sample:
        return None
    source = sample.get("sourceType") or "garmin"
    return body_to_row(sync_date, sample, source)


def open_worksheet() -> gspread.Worksheet:
    creds_raw = os.environ.get("GOOGLE_CREDS_JSON")
    if not creds_raw:
        raise ValueError("GOOGLE_CREDS_JSON is not set")

    info = json.loads(creds_raw)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_info(info, scopes=scopes)
    client = gspread.authorize(credentials)

    sheet_name = os.getenv("GOOGLE_SHEET_NAME", "Garmin BIA Data")
    try:
        spreadsheet = client.open(sheet_name)
    except gspread.SpreadsheetNotFound as exc:
        raise ValueError(
            f"Google Sheet '{sheet_name}' not found. "
            "Share it with the service account email from creds.json."
        ) from exc

    return spreadsheet.sheet1


def ensure_headers(worksheet: gspread.Worksheet) -> None:
    first_row = worksheet.row_values(1)
    if not first_row:
        worksheet.append_row(SHEET_HEADERS, value_input_option="USER_ENTERED")
        return
    if [cell.strip().lower() for cell in first_row] != SHEET_HEADERS:
        logger.warning(
            "Sheet header row does not match expected columns; upsert uses column A (date)."
        )


def upsert_row(worksheet: gspread.Worksheet, row: list[Any]) -> str:
    ensure_headers(worksheet)
    sync_date = str(row[0])
    dates = worksheet.col_values(1)
    for index, existing in enumerate(dates[1:], start=2):
        if existing == sync_date:
            worksheet.update(
                values=[row],
                range_name=f"A{index}:{_col_letter(len(row))}{index}",
                value_input_option="USER_ENTERED",
            )
            return "updated"

    worksheet.append_row(row, value_input_option="USER_ENTERED")
    return "appended"


def _col_letter(count: int) -> str:
    """1-based column index to letter (e.g. 11 -> K)."""
    letters = ""
    while count:
        count, remainder = divmod(count - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


def format_day_summary(sync_date: str, row: list[Any], action: str) -> str:
    weight, body_fat, muscle = row[1], row[3], row[6]
    parts = [f"{sync_date} ({action})"]
    if weight is not None:
        parts.append(f"{weight} kg")
    if body_fat is not None:
        parts.append(f"{body_fat}% fat")
    if muscle is not None:
        parts.append(f"{muscle} kg muscle")
    return " — ".join(parts)


def metrics_from_row(row: list[Any]) -> DayMetrics:
    return DayMetrics(
        weight_kg=_parse_number(row[1] if len(row) > 1 else None),
        body_fat_pct=_parse_number(row[3] if len(row) > 3 else None),
        muscle_mass_kg=_parse_number(row[6] if len(row) > 6 else None),
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

    history: dict[str, DayMetrics] = {}
    for row in values[1:]:
        if date_col >= len(row):
            continue
        day = str(row[date_col]).strip()
        if not day:
            continue
        history[day] = DayMetrics(
            weight_kg=_parse_number(row[weight_col] if weight_col < len(row) else None),
            body_fat_pct=_parse_number(row[bf_col] if bf_col < len(row) else None),
            muscle_mass_kg=_parse_number(row[muscle_col] if muscle_col < len(row) else None),
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
            values.append(value)
    return values


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _format_delta(current: float | None, previous: float | None, unit: str) -> str:
    if current is None or previous is None:
        return "(n/a)"
    diff = round(current - previous, 2)
    if diff > 0:
        return f"(+{diff}{unit})"
    if diff < 0:
        return f"({diff}{unit})"
    return f"(+0{unit})"


def pick_report_date(synced_dates: list[str], history: dict[str, DayMetrics]) -> date:
    """Anchor stats on today if present in sheet, else latest synced day."""
    today = today_local()
    today_key = today.isoformat()
    if today_key in synced_dates or today_key in history:
        return today
    if synced_dates:
        return max(date.fromisoformat(d) for d in synced_dates)
    return today


def format_telegram_report(
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

    this_window = _values_in_window(history, report_date, ROLLING_DAYS, "weight_kg")
    prior_end = report_date - timedelta(days=ROLLING_DAYS)
    prior_window = _values_in_window(history, prior_end, ROLLING_DAYS, "weight_kg")

    rolling_avg = _mean(this_window)
    prior_rolling_avg = _mean(prior_window)
    rolling_delta = (
        round(rolling_avg - prior_rolling_avg, 2)
        if rolling_avg is not None and prior_rolling_avg is not None
        else None
    )

    week_ago_key = (report_date - timedelta(days=ROLLING_DAYS)).isoformat()
    week_ago = history.get(week_ago_key)

    weight_line = (
        f'{cur_weight} kg {_format_delta(cur_weight, week_ago.weight_kg if week_ago else None, " kg")}'
        if cur_weight is not None
        else "n/a"
    )
    bf_line = (
        f'{cur_bf}% {_format_delta(cur_bf, week_ago.body_fat_pct if week_ago else None, "%")}'
        if cur_bf is not None
        else "n/a"
    )
    muscle_line = (
        f"{cur_muscle} kg "
        f"{_format_delta(cur_muscle, week_ago.muscle_mass_kg if week_ago else None, ' kg')}"
        if cur_muscle is not None
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
        f"current muscle: {muscle_line}"
    )


def today_local() -> date:
    tz_name = os.getenv("SYNC_TIMEZONE", "Asia/Kolkata")
    return datetime.now(ZoneInfo(tz_name)).date()


def dates_for_run() -> list[str]:
    """Dates to sync this run: single day if SYNC_DATE set, else lookback window."""
    explicit = os.getenv("SYNC_DATE")
    if explicit:
        return [explicit]

    lookback = max(1, int(os.getenv("SYNC_LOOKBACK_DAYS", "2")))
    end = today_local()
    return [
        (end - timedelta(days=offset)).isoformat()
        for offset in range(lookback - 1, -1, -1)
    ]


def main() -> int:
    sync_dates = dates_for_run()
    single_day_mode = os.getenv("SYNC_DATE") is not None

    try:
        garmin = credential_login_if_needed()
        worksheet = open_worksheet()
        synced_dates: list[str] = []
        synced_rows: dict[str, list[Any]] = {}

        for sync_date in sync_dates:
            row = fetch_bia_row(garmin, sync_date)
            if row is None:
                logger.info("No Garmin data for %s", sync_date)
                if single_day_mode:
                    message = f"No BIA/weight data for {sync_date} on Garmin Connect."
                    logger.warning(message)
                    send_telegram(message)
                continue

            action = upsert_row(worksheet, row)
            synced_dates.append(sync_date)
            synced_rows[sync_date] = row
            logger.info(format_day_summary(sync_date, row, action))

        if synced_dates:
            history = merge_synced_rows(load_sheet_history(worksheet), synced_rows)
            report_date = pick_report_date(synced_dates, history)
            message = format_telegram_report(history, report_date, status="OK")
            send_telegram(message)
            logger.info("Telegram report for %s:\n%s", report_date.isoformat(), message)
            return 0

        if single_day_mode:
            return 0

        logger.info(
            "No weigh-ins in sync window (%s); sheet unchanged.",
            ", ".join(sync_dates),
        )
        return 0

    except Exception as exc:
        error = f"Garmin BIA sync failed: {exc}"
        logger.exception(error)
        try:
            send_telegram(error)
        except Exception:
            logger.exception("Could not send Telegram error alert")
        return 1
