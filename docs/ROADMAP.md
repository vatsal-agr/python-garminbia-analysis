# Roadmap

Where **Garmin BIA Sync** is today, and how it could grow into a personal **Garmin intelligence** layer: your data, your sheet, optional live Garmin APIs via [python-garminconnect](https://github.com/cyberjunky/python-garminconnect), and a conversational Telegram + Gemini interface.

Status key: ✅ done · 🚧 in progress · 📋 planned · 💡 idea

---

## Vision

```text
Today (v1)     Garmin BIA  →  Sheet  →  scheduled Telegram digest

Future (v2+)   Garmin (wide)  →  your datastore  →  Telegram chat + Gemini
                              ↗                      ↘ charts, Q&A, coaching tone
                    python-garminconnect tools
```

**Not** a Garmin product clone — an unofficial, privacy-aware stack you control: sync what matters, ask questions in plain English, get analysis and charts on demand.

---

## Phase 0 — Foundation ✅ (current)

**v1.1:** Decision-oriented Telegram report (`TELEGRAM_LEGACY_REPORT` toggle, rolling FFM/FMI/muscle-weight, action line) — [`garmin_bia_sync/report.py`](../garmin_bia_sync/report.py).

| Item | Status |
|------|--------|
| Daily BIA sync to Google Sheets | ✅ |
| GitHub Actions cron (11:00 IST) + manual run | ✅ |
| Telegram digest (rolling avg, week deltas) | ✅ |
| Token-based Garmin auth for CI | ✅ |
| Local backfill script (`history_pull.py`, gitignored) | ✅ |

**Out of scope for v1 (by design):** two-way Telegram, AI, non-BIA Garmin metrics, always-on server.

---

## Phase 1 — Richer data layer 📋

**Goal:** Store more than one BIA row per day so later AI has context — without dumping 130 API responses into one sheet row.

### 1a — Sheet evolution (low effort)

- Extra BIA columns if Garmin exposes them consistently (e.g. physique rating, BMR).
- Second tab: `daily_summary` (steps, resting HR, sleep score, body battery min/max) — one row per day, curated columns only.

### 1b — Structured store (medium effort)

- **Option A:** Google Sheet with multiple tabs (`bia`, `sleep`, `activity`, `wellness`).
- **Option B:** SQLite / JSONL in repo-adjacent storage (S3, Drive file, or local) for time-series queries.
- Normalized schema: `date`, `metric`, `value`, `unit`, `source_api`.

### 1c — Sync expansion via python-garminconnect 📋

The upstream library exposes **130+ methods** (see their [demo.py](https://github.com/cyberjunky/python-garminconnect/blob/master/demo.py)). Prioritised batches for *personal* intelligence:

| Batch | Example APIs | Why it matters for AI |
|-------|----------------|------------------------|
| **Body** ✅ (partial) | `get_body_composition`, weight | Core — already in v1 |
| **Daily wellness** | `get_stats`, `get_user_summary`, steps, calories | “How was my week?” |
| **Sleep** | sleep duration, score, stages (account-dependent) | Recovery vs weight |
| **Heart** | resting HR, HRV | Trend + fatigue signals |
| **Stress / Body Battery** | `get_all_day_stress`, `get_body_battery` | Overtraining, lifestyle |
| **Activity** | recent activities, training load | Context for composition changes |
| **Hydration / SpO2** (if enabled) | hydration, pulse ox | Niche but easy to add |

Implementation sketch:

- New package: `garmin_bia_sync/collectors/` — one module per domain, each returns `list[MetricRow]`.
- Orchestrator: `python -m garmin_bia_sync --profile full` vs default BIA-only.
- Rate limits: stagger calls, reuse existing `GARMINTOKENS`, same GitHub secrets.

**Complexity:** medium (2–4 weeks part-time). **Risk:** Garmin API changes, per-account feature availability (404/empty).

---

## Phase 2 — Garmin Intelligence MVP (Telegram + Gemini) 📋

**Goal:** Chat with your bot: ask questions, get text analysis grounded in *your* history.

### Architecture

```text
You ──message──► Telegram ──webhook──► garmin_intelligence (new service)
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
              Sheet reader              Gemini API                 (optional) chart PNG
              / SQLite                flash / pro                 sendPhoto
```

**Hosting (pick one):**

| Option | Pros | Cons |
|--------|------|------|
| Cloud Run + webhook | Pay-per-use, scales to zero | Cold start |
| Fly.io / Railway | Simple always-on | Small monthly cost |
| Home (Pi / Mac) | Free | Uptime, TLS for webhook |

GitHub Actions stays **sync-only**; chat is a **separate deployable** (`garmin_intelligence/` or new repo).

### MVP feature set

| Feature | Description |
|---------|-------------|
| Auth | Only your `TELEGRAM_CHAT_ID` |
| `/status` | Latest row + 7-day avg (reuse v1 logic) |
| Free-text | “How did body fat trend since January?” → Gemini + sheet JSON |
| Context window | Last N days / summarised stats (not full sheet every message) |
| Secrets | `GEMINI_API_KEY`, existing Google + Telegram secrets |

### Gemini integration pattern

1. **System prompt:** coach persona, units (kg, %), disclaimers (not medical advice).
2. **User message** + **retrieved data** (structured JSON or markdown table).
3. **Response** → Telegram `sendMessage`.

**Complexity:** medium (~1–2 weekends for text-only MVP).

### Safety & privacy

- Document that message content + sheet excerpts go to **Google Gemini** API.
- Opt-in env flag: `INTELLIGENCE_ENABLED=true`.
- No training on your data (use Gemini API terms; avoid sending secrets).

---

## Phase 3 — Tools, charts, live Garmin 💡

**Goal:** “Visualise as I need” — charts in Telegram + smarter data retrieval.

### 3a — Function calling / tools

Give Gemini (or hard-coded intents) structured tools instead of raw sheet dumps:

| Tool | Behavior |
|------|----------|
| `get_metrics(metric, start, end)` | Query store |
| `rolling_avg(metric, days)` | Same math as v1 digest |
| `compare_periods(a, b)` | Week vs week |
| `plot(metric, start, end, chart_type)` | Returns PNG path → `sendPhoto` |

Libraries: `matplotlib` or `plotly` → static PNG for Telegram.

### 3b — Live python-garminconnect in chat

When sheet is stale or user asks “today so far”:

- Tool: `garmin_fetch(endpoint, date)` wrapping allowed library methods.
- Reuse `GARMINTOKENS` on the intelligence service (same as sync).
- Cache responses (e.g. 15 min) to avoid hammering Garmin.

### 3c — Proactive insights (optional)

- After daily sync, *optional* second message: Gemini-generated “one insight” if anomaly detected (weight spike, BF% step change).
- Toggle via env: `PROACTIVE_INSIGHTS=false` by default.

**Complexity:** medium–high (2–4 weeks cumulative).

---

## Phase 4 — Product polish 💡

| Item | Notes |
|------|--------|
| Conversation memory | Per-user thread id (Telegram user), last K turns |
| Cost controls | Max tokens / day, model routing (Flash vs Pro) |
| Multi-sheet / multi-user | Family scale — auth per chat_id |
| Web dashboard | Optional Canvas/React read-only charts (export from sheet) |
| Voice notes | Telegram voice → transcribe → Gemini (heavy, optional) |
| Open-source hygiene | Split `garmin_bia_sync` (sync) vs `garmin_intelligence` (bot) packages |

---

## Suggested repo layout (future)

```text
garmin_bia_sync/           # v1 — keep stable, cron-friendly
  sync.py
  collectors/              # Phase 1c
  store.py                 # Phase 1b

garmin_intelligence/       # Phase 2+
  webhook.py
  gemini_agent.py
  tools/
  charts.py

docs/
  setup.md
  ROADMAP.md               # this file
```

---

## Dependencies & credits (unchanged principle)

- **[python-garminconnect](https://github.com/cyberjunky/python-garminconnect)** (cyberjunky) — Garmin access; MIT.
- **gspread** — Sheets.
- **Phase 2+:** `google-genai` (Gemini SDK), **FastAPI** / **Flask** for webhook, optional **matplotlib**.

Always pin versions in `requirements-intelligence.txt` separate from minimal sync requirements.

---

## Decision log (for later you)

| Question | Recommendation |
|----------|----------------|
| Sheet vs DB? | Start Sheet tabs; move to SQLite when >10k rows or chat needs SQL |
| One repo or two? | One mono-repo, two packages until intelligence needs its own deploy cycle |
| All 130 APIs? | No — curate ~15–25 metrics that answer real questions |
| Gemini model? | `gemini-2.0-flash` for chat; Pro for long multi-month analysis |
| Replace digest? | Keep v1 cron message; intelligence is *additive* |

---

## Milestone summary

| Milestone | User-facing outcome | Effort |
|-----------|---------------------|--------|
| **v1** ✅ | Weigh in → sheet → morning Telegram stats | Done |
| **v1.1** | More metrics in sheet / second tab | Small |
| **v2.0** | Ask Telegram bot for analysis (text) | Medium |
| **v2.5** | Charts on demand in chat | Medium |
| **v3.0** | Live Garmin + tools + selective full API surface | Large |
| **v4.0** | Proactive coaching, polish, multi-user | Large |

---

## Contributing / picking up work

1. Choose a phase and open an issue with the milestone label (`v1.1`, `v2.0`, …).
2. Keep sync workflow green — intelligence must not break unattended cron.
3. New features behind env flags until stable.

Questions or want to start **Phase 2 MVP**? Begin with `garmin_intelligence/webhook.py` + sheet reader only — no Gemini until read path is solid.
