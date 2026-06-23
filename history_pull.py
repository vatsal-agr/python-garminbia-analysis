#!/usr/bin/env python3
"""
Backfill Garmin BIA into Google Sheet (local use).

  python history_pull.py --start 2026-06-10 --end 2026-06-22
  python history_pull.py --notify --notify-only --report-date 2026-06-22 --with-gemini

Loads .env, creds.json, and gmail_token.json from the project root when present.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date, timedelta

from garmin_bia_sync.analysis import (
    analysis_enabled,
    coach_error_telegram,
    generate_coach_message,
)
from garmin_bia_sync.local_env import load_local_env
from garmin_bia_sync.notify import send_telegram
from garmin_bia_sync.report import format_telegram_report, has_weigh_in, load_sheet_history
from garmin_bia_sync.sync import (
    credential_login_if_needed,
    fetch_bia_row,
    open_worksheet,
    upsert_row,
)

DEFAULT_START = date(2025, 11, 25)
DEFAULT_END = date(2026, 6, 2)


def iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def send_report_for_date(
    worksheet,
    report_date: date,
    *,
    with_gemini: bool,
    send_digest: bool = True,
) -> None:
    """Send Telegram digest and optional Gemini coach from sheet history."""
    history = load_sheet_history(worksheet)
    metrics = history.get(report_date.isoformat())
    if not has_weigh_in(metrics):
        print(f"No weigh-in on sheet for {report_date}; skipping Telegram.")
        return

    message = format_telegram_report(history, report_date)
    if send_digest:
        send_telegram(message)
        print(f"Telegram digest sent for {report_date.isoformat()}")

    if not with_gemini:
        return
    if not analysis_enabled():
        print("Gemini disabled (set GEMINI_ANALYSIS_ENABLED=true).")
        return

    try:
        coach = generate_coach_message(history, report_date, message)
        send_telegram(coach)
        print(f"Gemini coach sent ({len(coach)} chars)")
    except Exception as exc:
        print(f"Gemini failed: {exc}", file=sys.stderr)
        send_telegram(coach_error_telegram(exc))


def main() -> int:
    load_local_env()

    parser = argparse.ArgumentParser(description="Backfill Garmin BIA into Google Sheet")
    parser.add_argument(
        "--start",
        default=DEFAULT_START.isoformat(),
        help=f"First day inclusive (default {DEFAULT_START})",
    )
    parser.add_argument(
        "--end",
        default=DEFAULT_END.isoformat(),
        help=f"Last day inclusive (default {DEFAULT_END})",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=3.0,
        help="Seconds between Garmin API calls (default 3.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch only; do not write to the sheet",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="After backfill, send Telegram digest for --report-date",
    )
    parser.add_argument(
        "--report-date",
        help="Digest date (YYYY-MM-DD); default is --end",
    )
    parser.add_argument(
        "--with-gemini",
        action="store_true",
        help="Send Gemini coach message (requires GEMINI_API_KEY)",
    )
    parser.add_argument(
        "--notify-only",
        action="store_true",
        help="Skip Garmin fetch; send Telegram/Gemini from sheet for --report-date",
    )
    parser.add_argument(
        "--gemini-only",
        action="store_true",
        help="With --notify-only: send coach message only (no digest repeat)",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start > end:
        print("start must be on or before end", file=sys.stderr)
        return 1

    if args.notify_only:
        if not args.notify:
            print("--notify-only requires --notify", file=sys.stderr)
            return 1
        report_day = date.fromisoformat(args.report_date) if args.report_date else end
        worksheet = open_worksheet()
        send_report_for_date(
            worksheet,
            report_day,
            with_gemini=args.with_gemini,
            send_digest=not args.gemini_only,
        )
        return 0

    total_days = (end - start).days + 1
    print(f"Backfill {start} → {end} ({total_days} days)")
    if args.dry_run:
        print("DRY RUN — no sheet writes")

    garmin = credential_login_if_needed()
    worksheet = None if args.dry_run else open_worksheet()

    synced = 0
    skipped = 0
    errors = 0

    for day in iter_dates(start, end):
        iso = day.isoformat()
        try:
            row = fetch_bia_row(garmin, iso)
            if row is None:
                skipped += 1
                print(f"  {iso}: no data")
            elif args.dry_run:
                synced += 1
                print(f"  {iso}: would sync (weight {row[1]} kg)")
            else:
                action = upsert_row(worksheet, row)
                synced += 1
                print(f"  {iso}: {action} — {row[1]} kg")
        except Exception as exc:
            errors += 1
            print(f"  {iso}: ERROR — {exc}", file=sys.stderr)

        time.sleep(args.delay)

    print(
        f"\nFinished. {synced} with data, {skipped} empty, {errors} errors "
        f"(of {total_days} days)."
    )

    if args.notify and not args.dry_run:
        report_day = date.fromisoformat(args.report_date) if args.report_date else end
        if worksheet is None:
            worksheet = open_worksheet()
        send_report_for_date(
            worksheet,
            report_day,
            with_gemini=args.with_gemini,
        )

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
