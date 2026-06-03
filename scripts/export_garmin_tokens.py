#!/usr/bin/env python3
"""Print Garmin token JSON for the GARMINTOKENS GitHub secret (after a local login)."""

import os
import sys
from pathlib import Path

DEFAULT = Path.home() / ".garminconnect" / "garmin_tokens.json"


def main() -> int:
    path = Path(
        sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GARMINTOKENS", DEFAULT)
    ).expanduser()
    if path.is_dir():
        path = path / "garmin_tokens.json"

    if not path.is_file():
        print(
            "No token file found. Run `python garmin_sync.py` locally first "
            "(complete MFA once).",
            file=sys.stderr,
        )
        print(f"Expected: {path}", file=sys.stderr)
        return 1

    print(path.read_text())
    print(
        "\n# Copy the JSON above into GitHub → Settings → Secrets → GARMINTOKENS",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
