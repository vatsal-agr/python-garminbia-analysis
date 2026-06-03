# Garmin BIA → Google Sheets → Telegram

Step-by-step execution guide. **You** own credentials and accounts; **this repo** has the script and GitHub workflow.

## Overview

| Phase | Who | What |
|-------|-----|------|
| 1 | You | Telegram bot, Google Cloud, Google Sheet |
| 2 | You + script | Local install, first MFA login, verify sheet + Telegram |
| 3 | You | Private GitHub repo + secrets (no `creds.json` in git) |
| 4 | You | Push workflow, manual run, then daily cron |

---

## Phase 1 — Accounts (≈30 min)

### Telegram

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → save **bot token**.
2. Message [@userinfobot](https://t.me/userinfobot) → save **chat ID**.
3. Open a chat with your new bot and send any message (activates the channel).

### Google Cloud + Sheet

1. [Google Cloud Console](https://console.cloud.google.com/) → new project.
2. Enable **Google Sheets API** and **Google Drive API**.
3. **IAM → Service accounts** → create → **Keys** → JSON → save as `creds.json` in this folder (gitignored).
4. Create a sheet named **Garmin BIA Data** (or any name — set `GOOGLE_SHEET_NAME`).
5. Copy `client_email` from `creds.json` → share the sheet with that email as **Editor**.

---

## Phase 2 — Local test (do this before GitHub)

```bash
cd /Users/vatsal/python-garminbia-analysis
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set environment variables (copy from `.env.example`):

```bash
export GARMIN_EMAIL="your@email.com"
export GARMIN_PASSWORD="your-password"
export TELEGRAM_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
export GOOGLE_CREDS_JSON="$(cat creds.json)"
export GOOGLE_SHEET_NAME="Garmin BIA Data"
```

Run once (you will be prompted for **Garmin MFA** the first time):

```bash
python garmin_sync.py
```

**Success criteria**

- New or updated row in the sheet (columns: date, weight, body fat, etc.).
- Telegram message: `Garmin BIA sync OK`.
- Token file created: `~/.garminconnect/garmin_tokens.json`.

Export tokens for GitHub (after a successful local run):

```bash
python scripts/export_garmin_tokens.py
```

Copy the printed JSON — you will paste it into the `GARMINTOKENS` secret.

---

## Phase 3 — GitHub repository

1. Create a **private** repo and push this project (without `creds.json` or `.env`).
2. **Settings → Secrets and variables → Actions → Secrets**:

| Secret | Value |
|--------|--------|
| `GARMIN_EMAIL` | Garmin login email |
| `GARMIN_PASSWORD` | Garmin password |
| `GARMINTOKENS` | Full JSON from `export_garmin_tokens.py` |
| `TELEGRAM_TOKEN` | Bot token |
| `TELEGRAM_CHAT_ID` | Your chat ID |
| `GOOGLE_CREDS_JSON` | Entire `creds.json` file contents |

3. Optional **Variables** (not secrets): `GOOGLE_SHEET_NAME` = exact sheet title if not `Garmin BIA Data`.

**Important:** With `GARMINTOKENS` set, Actions should **not** need MFA. Keep email/password secrets as fallback if tokens expire.

---

## Phase 4 — Automation

The workflow lives at `.github/workflows/daily_sync.yml`.

1. Push to GitHub.
2. **Actions** tab → **Daily Garmin BIA Sync** → **Run workflow**.
3. Green run + Telegram message = live.

Default schedule: **07:00 UTC** daily. Edit the `cron` line in the YAML for your timezone (cron is always UTC).

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| MFA in GitHub Actions | Run locally once; set `GARMINTOKENS` secret from `scripts/export_garmin_tokens.py`. |
| Sheet not found | Sheet title must match `GOOGLE_SHEET_NAME`; service account must be Editor. |
| No data for today | Weigh in on Garmin (Index scale / BIA) for that calendar day; or set `SYNC_DATE=YYYY-MM-DD`. |
| Telegram silent | Check token, chat ID, and that you messaged the bot once. |
| Tokens expired | Re-run `python garmin_sync.py` locally; update `GARMINTOKENS` secret. |

---

## What you do next (checklist)

- [ ] Phase 1: Telegram + Google Sheet + `creds.json`
- [ ] Phase 2: `pip install` → `python garmin_sync.py` → verify sheet + Telegram
- [ ] Phase 2b: `python scripts/export_garmin_tokens.py` → save output
- [ ] Phase 3: Private repo + all secrets
- [ ] Phase 4: Manual workflow run → confirm cron time

When Phase 1 is done, say which step you are on and we can debug the first local run together.
