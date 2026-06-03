#!/usr/bin/env python3
"""
One-off backfill from Garmin → Google Sheet. Local use only (gitignored).

  source .venv/bin/activate
  export GOOGLE_CREDS_JSON="$(cat creds.json)"   # plus Garmin/Telegram vars if needed
  python history_pull.py

Defaults: 2025-11-25 through 2026-06-02 (IST calendar days).
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from garmin_bia_sync.sync import (
    credential_login_if_needed,
    fetch_bia_row,
    open_worksheet,
    upsert_row,
)

DEFAULT_START = date(2025, 11, 25)
DEFAULT_END = date(2026, 6, 2)


def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def iter_dates(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def main() -> int:
    _load_dotenv()

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
        help="Seconds between Garmin API calls (default 1.0)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch only; do not write to the sheet",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start > end:
        print("start must be on or before end", file=sys.stderr)
        return 1

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
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
