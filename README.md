# Garmin BIA Sync

Weigh in on your Garmin Index (or any Garmin flow that records body composition). Later—on a schedule or after a manual run—you get a **Telegram** summary of how you’re doing: current weight, body fat, muscle mass, a **7-day rolling average**, and week-over-week deltas. Everything is stored in **Google Sheets** so you own the history.

No Garmin app checking. No spreadsheet formulas to maintain. One small Python job, free to run on GitHub Actions.

```
Garmin Connect  →  sync  →  Google Sheet  →  Telegram (data digest + optional Gemini coach)
```

---

## What you get

- **Automatic daily sync** (GitHub Actions) or run locally on demand
- **One row per day** in Google Sheets: weight, BMI, body fat %, body water %, bone mass, muscle mass, visceral fat, metabolic age
- **Smart catch-up**: each run syncs yesterday + today so a weigh-in *after* your cron time still lands on the next run
- **Telegram digest** after a successful sync (default: **decision** format):

  ```
  Date: 2026-06-03

  7d avg weight: 67.15 kg (+0.14 kg) ✅
  7d avg FFM:    56.02 kg (+0.08 kg)
  7d avg FMI:    5.21 kg/m² (-0.06)
  7d muscle/wt:  46.0% (+0.2 pp)

  → On track
  ```

  Set GitHub variable `TELEGRAM_LEGACY_REPORT=true` for the older format with raw daily readings. Index S2 does not report visceral fat (that line is omitted automatically).

- **Optional second message (Gemini):** evidence-based daily coach narrative (Dr. Mike / RP–style lens) over your sheet history — enable with `GEMINI_ANALYSIS_ENABLED=true` and secret `GEMINI_API_KEY`.

---

## Requirements

| Piece | Purpose |
|-------|---------|
| [Garmin Connect](https://connect.garmin.com/) | Source of BIA / weight data |
| [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) | Unofficial Garmin Connect API (see [Credits](#credits)) |
| Google Cloud service account | Write to Sheets via [gspread](https://github.com/burnash/gspread) |
| Telegram bot | Notifications ([@BotFather](https://t.me/BotFather)) |
| Python **3.12+** | Runtime |
| GitHub account (optional) | Free scheduled runs |

---

## Quick start

```bash
git clone https://github.com/YOUR_USER/python-garminbia-analysis.git
cd python-garminbia-analysis

python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

1. Copy `.env.example` → `.env` and fill in Garmin, Telegram, and Google settings.
2. Save your Google service account JSON as `creds.json` (gitignored) and share your spreadsheet with the service account email.
3. First sync (Garmin MFA once; tokens saved under `~/.garminconnect/`):

   ```bash
   export GOOGLE_CREDS_JSON="$(cat creds.json)"
   python -m garmin_bia_sync
   ```

4. For GitHub Actions, export tokens: `python scripts/export_tokens.py` → repository secret `GARMINTOKENS`.

Full walkthrough: **[docs/setup.md](docs/setup.md)** (Telegram, GCP, secrets, cron, troubleshooting).

**Roadmap:** **[docs/ROADMAP.md](docs/ROADMAP.md)** — Garmin intelligence (Telegram + Gemini), broader [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) metrics.

---

## Project layout

```
garmin_bia_sync/          Main package
  sync.py
  report.py               Numeric Telegram digest
  analysis.py             Optional Gemini coach (message 2)
  notify.py               Telegram send
scripts/
  export_tokens.py        Print token JSON for GitHub Secrets
docs/
  setup.md                Step-by-step setup
  ROADMAP.md              Future: AI chat, extra Garmin APIs
.github/workflows/
  daily_sync.yml          Scheduled sync (default 11:00 IST)
```

---

## Configuration

| Variable | Description |
|----------|-------------|
| `GARMIN_EMAIL` / `GARMIN_PASSWORD` | Garmin login (first run / token refresh) |
| `GARMINTOKENS` | Local path or full token JSON (GitHub secret for CI) |
| `GOOGLE_CREDS_JSON` | Service account JSON as a string |
| `GOOGLE_SHEET_NAME` | Spreadsheet title (default: `Garmin BIA Data`) |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | Bot notifications |
| `SYNC_TIMEZONE` | Calendar timezone (default: `Asia/Kolkata`) |
| `SYNC_LOOKBACK_DAYS` | Days synced per run (default: `2` = yesterday + today) |
| `SYNC_DATE` | Optional single-day backfill (`YYYY-MM-DD`) |
| `TELEGRAM_LEGACY_REPORT` | `true` = legacy digest; unset/`false` = decision report (default) |
| `USER_HEIGHT_CM` | Height in cm for FMI in decision report (e.g. `184`) |
| `WEIGHT_TARGET_MIN_KG` / `WEIGHT_TARGET_MAX_KG` | Weekly weight-rate band for status (default `0.10`–`0.20` kg) |
| `GEMINI_ANALYSIS_ENABLED` | `true` = send coach message after data digest |
| `GEMINI_API_KEY` | Google AI Studio key (GitHub **secret**, not variable) |
| `GEMINI_MODEL` | Optional (default `gemini-3.5-flash`) |
| `ANALYSIS_LOOKBACK_DAYS` | Days of sheet history sent to Gemini (default `28`) |
| `USER_GOAL` | Your goal text for the coach (lean bulk, etc.) |

Default GitHub cron: **11:00 IST** (`30 5 * * *` UTC). Edit `.github/workflows/daily_sync.yml` to change it.

---

## Sheet columns

| Column | Source |
|--------|--------|
| `date` | Calendar day |
| `weight_kg` | Garmin weight (grams → kg when needed) |
| `bmi` | BMI |
| `body_fat_pct` | Body fat % |
| `body_water_pct` | Body water % |
| `bone_mass_kg` | Bone mass |
| `muscle_mass_kg` | Muscle mass |
| `visceral_fat` | Visceral fat |
| `metabolic_age` | Metabolic age |
| `source` | Garmin source type |
| `synced_at_utc` | Last write time |

Re-runs **update** the row for that date; they do not duplicate.

---

## Security notes (public repos)

- Never commit `creds.json`, `.env`, or `~/.garminconnect/` tokens.
- Use **GitHub repository secrets** for CI (not environment files in the repo).
- This project is **not affiliated with Garmin**. You use your own credentials at your own risk.

---

## Credits

This project would not exist without **[python-garminconnect](https://github.com/cyberjunky/python-garminconnect)** — an excellent unofficial Garmin Connect API for Python, maintained by **[cyberjunky](https://github.com/cyberjunky)** (Ron Klinkien) and contributors. Garmin BIA Sync only orchestrates that library with Google Sheets, Telegram, and GitHub Actions.

- **python-garminconnect**: [github.com/cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect) · [PyPI](https://pypi.org/project/garminconnect/) · MIT License  
- **gspread** · Google Sheets access  
- **Telegram Bot API** · notifications  

If you use this repo, consider starring or supporting the upstream [python-garminconnect](https://github.com/cyberjunky/python-garminconnect) project as well.

---

## Disclaimer

Garmin Connect has no official public API for personal scripts. Authentication and endpoints may change. This tool is for personal use; review Garmin’s terms of service before relying on it.

---

## License

Application code in this repository: **MIT** (see [LICENSE](LICENSE) if present).

Third-party packages retain their own licenses (notably **python-garminconnect**, MIT).
