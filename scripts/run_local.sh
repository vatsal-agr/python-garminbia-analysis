#!/usr/bin/env bash
# Local sync: load .env plus creds.json / gmail_token.json, then run the job.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -f creds.json ]]; then
  export GOOGLE_CREDS_JSON="$(cat creds.json)"
fi

if [[ -f gmail_token.json ]]; then
  export GMAIL_OAUTH_JSON="$(cat gmail_token.json)"
fi

if [[ -z "${GOOGLE_CREDS_JSON:-}" ]]; then
  echo "GOOGLE_CREDS_JSON missing — add creds.json or set it in .env" >&2
  exit 1
fi

PYTHON="${ROOT}/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then
  PYTHON="python3"
fi

if [[ $# -eq 0 ]]; then
  exec "$PYTHON" -m garmin_bia_sync
fi

exec "$PYTHON" "$@"
