"""Gemini daily coach analysis (second Telegram message)."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import date, timedelta
from typing import Any

from garmin_bia_sync.report import (
    DayMetrics,
    ROLLING_DAYS,
    today_has_weigh_in,
    _ffm,
    _fmi,
    _ffm_values_in_window,
    _fmi_values_in_window,
    _muscle_ratio_values_in_window,
    _muscle_weight_pct,
    _rolling_delta,
    _user_height_cm,
    _values_in_window,
)

logger = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 28
DEFAULT_MODEL = "gemini-3.5-flash"
# Fallbacks if primary model is unavailable on this API key/tier.
_MODEL_FALLBACKS = (
    "gemini-3.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
)
def analysis_enabled() -> bool:
    return os.getenv("GEMINI_ANALYSIS_ENABLED", "false").lower() in (
        "true",
        "1",
        "yes",
    )


def _lookback_days() -> int:
    raw = os.getenv("ANALYSIS_LOOKBACK_DAYS")
    if raw:
        return max(7, int(raw))
    return DEFAULT_LOOKBACK_DAYS


def _model_name() -> str:
    return os.getenv("GEMINI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _models_to_try() -> list[str]:
    primary = _model_name()
    seen: set[str] = set()
    ordered: list[str] = []
    for name in (primary, *_MODEL_FALLBACKS):
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ordered


def _user_goal() -> str:
    return os.getenv(
        "USER_GOAL",
        "Lean bulk: target roughly +0.10 to +0.20 kg per week on 7-day average weight; "
        "prioritise upward weight trend while keeping fat gain reasonable.",
    ).strip()


def _filter_history(
    history: dict[str, DayMetrics],
    report_date: date,
    lookback: int,
) -> dict[str, DayMetrics]:
    start = report_date - timedelta(days=lookback)
    filtered: dict[str, DayMetrics] = {}
    for key, metrics in history.items():
        try:
            day = date.fromisoformat(key)
        except ValueError:
            continue
        if start <= day <= report_date:
            filtered[key] = metrics
    return dict(sorted(filtered.items()))


def _enrich_day(key: str, metrics: DayMetrics) -> dict[str, Any]:
    height = _user_height_cm()
    row: dict[str, Any] = {
        "date": key,
        "weight_kg": metrics.weight_kg,
        "body_fat_pct": metrics.body_fat_pct,
        "muscle_mass_kg": metrics.muscle_mass_kg,
        "ffm_kg": _ffm(metrics.weight_kg, metrics.body_fat_pct),
        "muscle_weight_pct": _muscle_weight_pct(metrics),
    }
    if height is not None:
        row["fmi_kg_m2"] = _fmi(metrics.weight_kg, metrics.body_fat_pct, height)
    return row


def build_analysis_payload(
    history: dict[str, DayMetrics],
    report_date: date,
    data_message: str,
) -> dict[str, Any]:
    """Structured context for Gemini (compact to stay within free-tier tokens)."""
    lookback = _lookback_days()
    window = _filter_history(history, report_date, lookback)

    height = _user_height_cm()
    weight_collector = lambda h, e, d: _values_in_window(h, e, d, "weight_kg")

    weight_avg, weight_delta = _rolling_delta(
        history, report_date, ROLLING_DAYS, weight_collector
    )
    ffm_avg, ffm_delta = _rolling_delta(
        history, report_date, ROLLING_DAYS, _ffm_values_in_window
    )

    fmi_avg = fmi_delta = None
    if height is not None:
        fmi_collector = lambda h, e, d: _fmi_values_in_window(h, e, d, height)
        fmi_avg, fmi_delta = _rolling_delta(
            history, report_date, ROLLING_DAYS, fmi_collector
        )

    muscle_avg, muscle_delta = _rolling_delta(
        history, report_date, ROLLING_DAYS, _muscle_ratio_values_in_window
    )

    last_14 = _filter_history(history, report_date, 14)
    weigh_ins_14d = sum(
        1 for m in last_14.values() if m.weight_kg is not None
    )

    return {
        "report_date": report_date.isoformat(),
        "user_height_cm": height,
        "user_goal": _user_goal(),
        "device_note": (
            "Garmin Index S2 BIA: weight, body fat %, muscle mass. "
            "BF% swings with hydration; no visceral on S2."
        ),
        "today_data_digest": data_message,
        "rolling_7d_vs_prior_7d": {
            "weight_kg_avg": weight_avg,
            "weight_kg_delta": weight_delta,
            "ffm_kg_avg": ffm_avg,
            "ffm_kg_delta": ffm_delta,
            "fmi_kg_m2_avg": fmi_avg,
            "fmi_kg_m2_delta": fmi_delta,
            "muscle_weight_pct_avg": muscle_avg,
            "muscle_weight_pct_delta": muscle_delta,
        },
        "weigh_ins_last_14_days": weigh_ins_14d,
        "daily_series": [_enrich_day(k, m) for k, m in window.items()],
    }


def _system_prompt() -> str:
    return """You are writing Telegram message 2 for one lifter. Message 1 already sent the numeric digest
(today_data_digest) — interpret it; do not replace it with a copy-paste.

Start message 2 with the 📊 Verdict label. No text before it.

GOAL:
- Lean bulk. Target weight rate: +0.10 to +0.20 kg/week.
- Target FFM: trending up or flat over 7+ days.
- Target BF%: flat or declining over 4+ weeks.
- Target muscle/weight %: flat or rising.

VOICE:
- Second person ("you"). One sharp training partner who knows BIA limits.
- Apply an evidence-based hypertrophy lens (rolling 7d trends beat daily noise; lean-bulk rate targets).
- No greetings, intros, sign-offs, disclaimers, or role-play openers.

DATA RULES:
- Garmin Index S2: weight, BF%, muscle mass are estimates; hydration shifts FFM/FMI/BF% on the same scale.
- BF% swings ≤ ±1.5 pp day-over-day are hydration noise by default. FFM delta requires corroboration
  from both weight trend and muscle/weight % trend before calling it real tissue change.
- No visceral fat on S2 — ignore empty fields.
- today_data_digest math is authoritative; if you disagree, explain the mechanism (e.g. water in FFM,
  meal timing).
- Use rolling_7d_vs_prior_7d and daily_series; cite actual numbers with units (kg, kg/m², pp).

DEPTH — every message must include:
1) Verdict vs goal: on track / ahead / behind / unclear — tied to weight rate and deltas.
2) Signal vs noise: for each meaningful delta (weight, FFM, FMI, muscle/wt %), judge real tissue vs
   hydration/measurement. BF% swings ≤ ±1.5 pp = hydration noise by default. FFM needs corroboration
   from weight trend AND muscle/wt% before calling it real. 1–2 sentences per metric that matters.
3) Pattern read: cite weigh_ins_last_14_days count; call out BF% or weight volatility in daily_series
   if present.
4) Goal math: compare observed weekly weight change to +0.10–0.20 kg/week target. State calorie
   direction implied (surplus/deficit/hold) without medical claims.
5) Next 7 days — pick the highest-priority lever that applies, in this order:
   - If weight rate is off-target → lever is calories (specify direction and rough magnitude).
   - If weight rate is on-target but FFM is flat or declining → lever is training (volume or
     protein distribution).
   - If both are on-target but weigh-in count < 5 of last 7 days → lever is consistency.
   Do not default to a generic "stay consistent" if a higher-priority lever applies.

FORMAT (plain text only — no markdown):
- Do NOT use **bold**, *italic*, __underline__, or # headings.
- Use the emoji section labels below exactly.
- For bullet lists under 🔍 Likely drivers, start each line with "• " (not asterisk).

📊 Verdict: (1–2 sentences, numbers inline)
📈 What changed: (short paragraphs; explain deltas, not just list them)
🔍 Likely drivers: (3–5 bullets — training, intake, sleep, hydration, measurement)
🎯 Next 7 days: (one short paragraph, one lever, actionable)
Confidence: [High / Medium / Low] — one sentence.
  High   = 6–7 weigh-ins in last 7 days, weight CV < 1%
  Medium = 4–5 weigh-ins, or CV 1–2%
  Low    = ≤3 weigh-ins, or CV > 2%

~400–550 words max."""


def sanitize_coach_text(text: str) -> str:
    """Strip markdown Gemini adds; Telegram sendMessage uses plain text (no parse_mode)."""
    cleaned = text.strip()
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", cleaned)
    cleaned = re.sub(r"(?m)^\* ", "• ", cleaned)
    return cleaned.strip()


def _is_quota_error(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "429" in text or "quota" in text or "resource_exhausted" in text


def coach_error_telegram(exc: BaseException) -> str:
    if _is_quota_error(exc):
        model = _model_name()
        return (
            "Coach analysis skipped: Gemini API quota for this key/model.\n\n"
            f"Model tried: {model} (and fallbacks). Free tier may not include "
            "gemini-2.0-flash — unset GEMINI_MODEL or use gemini-3.5-flash.\n"
            "Check https://aistudio.google.com/apikey and billing tier.\n\n"
            "Your numeric digest above is still valid."
        )
    return (
        f"Coach analysis unavailable today ({type(exc).__name__}).\n\n"
        "Your numeric digest above is still valid."
    )


def _extract_response_text(response: Any) -> str:
    text = (getattr(response, "text", None) or "").strip()
    if text:
        return text
    raise ValueError("Gemini returned an empty response")


def _call_gemini_once(client: Any, model: str, prompt: str) -> str:
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=_system_prompt(),
            temperature=0.4,
            max_output_tokens=8192,
        ),
    )
    return _extract_response_text(response)


def _call_gemini(prompt: str) -> str:
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY is not set")

    from google import genai
    from google.genai import errors as genai_errors

    client = genai.Client(api_key=api_key)
    last_exc: BaseException | None = None

    for model in _models_to_try():
        for attempt in range(2):
            try:
                logger.info("Gemini coach request: model=%s attempt=%s", model, attempt + 1)
                return _call_gemini_once(client, model, prompt)
            except genai_errors.ClientError as exc:
                last_exc = exc
                if _is_quota_error(exc) and attempt == 0:
                    logger.warning("Gemini quota/rate limit for %s, retrying in 12s", model)
                    time.sleep(12)
                    continue
                logger.warning("Gemini failed for model %s: %s", model, exc)
                break
            except Exception as exc:
                last_exc = exc
                logger.warning("Gemini failed for model %s: %s", model, exc)
                break

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Gemini call failed with no models configured")


def generate_coach_message(
    history: dict[str, DayMetrics],
    report_date: date,
    data_message: str,
) -> str:
    """Message 2: analyst narrative."""
    payload = build_analysis_payload(history, report_date, data_message)
    user_prompt = (
        "Write message 2 only. Start with the 📊 Verdict line — no intro before it.\n"
        "Plain text only: no **bold**, no *italic*, no markdown.\n"
        "JSON:\n"
        f"{json.dumps(payload, separators=(',', ':'))}"
    )
    return sanitize_coach_text(_call_gemini(user_prompt))


def maybe_send_coach_analysis(
    history: dict[str, DayMetrics],
    report_date: date,
    data_message: str,
) -> None:
    if not analysis_enabled():
        logger.info("Gemini analysis disabled (GEMINI_ANALYSIS_ENABLED)")
        return

    if not today_has_weigh_in(history):
        logger.info("Skipping Gemini coach: no weigh-in today")
        return

    from garmin_bia_sync.notify import send_telegram

    try:
        coach = generate_coach_message(history, report_date, data_message)
        send_telegram(coach)
        logger.info("Gemini coach analysis sent (%s chars)", len(coach))
    except Exception as exc:
        logger.error("Gemini analysis failed: %s", exc)
        send_telegram(coach_error_telegram(exc))
