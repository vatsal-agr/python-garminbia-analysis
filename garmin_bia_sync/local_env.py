"""Load .env and local credential files for scripts (never commit secrets)."""

from __future__ import annotations

import os
from pathlib import Path


def load_local_env(root: Path | None = None) -> None:
    """Load .env, creds.json, and gmail_token.json when present."""
    base = root or Path.cwd()
    env_path = base / ".env"
    if env_path.is_file():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and not os.environ.get(key):
                os.environ[key] = value

    creds = base / "creds.json"
    if not os.environ.get("GOOGLE_CREDS_JSON") and creds.is_file():
        os.environ["GOOGLE_CREDS_JSON"] = creds.read_text()

    gmail = base / "gmail_token.json"
    if not os.environ.get("GMAIL_OAUTH_JSON") and gmail.is_file():
        os.environ["GMAIL_OAUTH_JSON"] = gmail.read_text()
