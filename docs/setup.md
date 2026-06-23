# Setup guide

## Overview

| Phase | What |
|-------|------|
| 1 | Telegram bot, Google Cloud, Google Sheet |
| 2 | Local install, first MFA login, verify sheet + Telegram |
| 3 | Private GitHub repo + repository secrets |
| 4 | Push workflow, manual run, then daily cron |

---

## Phase 1 — Accounts

### Telegram

1. Message [@BotFather](https://t.me/BotFather) → `/newbot` → save **bot token**.
2. Message [@userinfobot](https://t.me/userinfobot) → save **chat ID**.
3. Open a chat with your new bot and send any message (activates the channel).

### Google Cloud + Sheet

1. [Google Cloud Console](https://console.cloud.google.com/) → new project.
2. Enable **Google Sheets API** and **Google Drive API**.
3. **IAM → Service accounts** → create → **Keys** → JSON → save as `creds.json` in the project root (gitignored).
4. Create a sheet named **Garmin BIA Data** (or any name — set `GOOGLE_SHEET_NAME`).
5. Copy `client_email` from `creds.json` → share the sheet with that email as **Editor**.

---

## Phase 2 — Local test

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Set environment variables (see `.env.example`):

```bash
export GARMIN_EMAIL="your@email.com"
export GARMIN_PASSWORD="your-password"
export TELEGRAM_TOKEN="..."
export TELEGRAM_CHAT_ID="..."
export GOOGLE_CREDS_JSON="$(cat creds.json)"
export GOOGLE_SHEET_NAME="Garmin BIA Data"
```

Run once (Garmin **MFA** on first run):

```bash
python -m garmin_bia_sync
```

**Success criteria**

- Row in the sheet (weight, body fat, muscle, etc.).
- Telegram: decision report (7d rolling weight, FFM, FMI, muscle/wt, action line).
- Token file: `~/.garminconnect/garmin_tokens.json`.

Export tokens for GitHub:

```bash
python scripts/export_tokens.py
```

Paste the JSON into the `GARMINTOKENS` repository secret.

---

## Phase 3 — GitHub repository

1. Create a **private** repo and push (no `creds.json` or `.env`).
2. **Settings → Secrets and variables → Actions → Repository secrets**:

| Secret | Value |
|--------|--------|
| `GARMIN_EMAIL` | Garmin login email |
| `GARMIN_PASSWORD` | Garmin password |
| `GARMINTOKENS` | Full JSON from `scripts/export_tokens.py` (optional if using Gmail OTP) |
| `TELEGRAM_TOKEN` | Bot token |
| `TELEGRAM_CHAT_ID` | Your chat ID |
| `GOOGLE_CREDS_JSON` | Entire `creds.json` contents |
| `GMAIL_OAUTH_JSON` | Gmail OAuth user JSON (`gmail.readonly`) for automatic Garmin MFA on CI |

Optional **repository variables** (Settings → Actions → Variables):

| Variable | Purpose |
|----------|---------|
| `GOOGLE_SHEET_NAME` | Spreadsheet title if not `Garmin BIA Data` |
| `USER_HEIGHT_CM` | Your height in cm (for FMI in decision Telegram) |
| `TELEGRAM_LEGACY_REPORT` | `true` = old digest with daily readings; unset = decision format |
| `WEIGHT_TARGET_MIN_KG` / `WEIGHT_TARGET_MAX_KG` | Lean-bulk band for weight status (default 0.10–0.20 kg/week) |
| `GEMINI_ANALYSIS_ENABLED` | `true` to send a second Telegram message (Gemini coach) |
| `GEMINI_MODEL` | Optional (default `gemini-3.5-flash`) |
| `ANALYSIS_LOOKBACK_DAYS` | History depth for coach (default `28`) |
| `USER_GOAL` | Free-text goal for the analyst prompt |

**Secret:** `GEMINI_API_KEY` from [Google AI Studio](https://aistudio.google.com/apikey).

With `GARMINTOKENS` set, Actions should not prompt for MFA. Keep email/password as fallback when tokens expire.

**Gmail OTP (recommended for CI):** Set `GMAIL_OAUTH_JSON` to the JSON from `gmail_token.json` (see `jsonscript.py`). Enable Gmail API on your Google Cloud project. Each run logs in fresh with `GARMIN_EMAIL`/`GARMIN_PASSWORD` and reads the Garmin verification email automatically. You can remove the `GARMINTOKENS` secret when using this flow.

**Index S2:** does not measure visceral fat; the visceral line appears only if your sheet has that data (other scales).

---

## Phase 4 — Automation

Workflow: `.github/workflows/daily_sync.yml`

1. Push to GitHub.
2. **Actions** → **Daily Garmin BIA Sync** → **Run workflow**.
3. Green run + Telegram message = live.

Default schedule: **11:00 IST** (`30 5 * * *` UTC). Edit `cron` in the workflow if needed (always UTC).

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| MFA in GitHub Actions | Set `GMAIL_OAUTH_JSON` (Gmail OTP) or run locally and update `GARMINTOKENS`. |
| Sheet not found | Match `GOOGLE_SHEET_NAME`; service account must be Editor. |
| No data for today | At 11:00 IST today may be empty if you weigh later — expected. Yesterday is retried via `SYNC_LOOKBACK_DAYS=2`. |
| Late weigh-in after 11:00 | Appears on the next run (yesterday’s date in the lookback window). |
| Telegram silent | Check token, chat ID; message the bot once. |
| Tokens expired | Re-run locally; update `GARMINTOKENS`. |

---

## Checklist

- [ ] Phase 1: Telegram + Google Sheet + `creds.json`
- [ ] Phase 2: `pip install` → `python -m garmin_bia_sync` → sheet + Telegram
- [ ] Phase 2b: `python scripts/export_tokens.py`
- [ ] Phase 3: Private repo + secrets
- [ ] Phase 4: Manual workflow run
