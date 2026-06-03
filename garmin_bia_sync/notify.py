"""Telegram delivery."""

from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

TELEGRAM_MAX_LENGTH = 4096


def send_telegram(message: str) -> None:
    token = os.environ.get("TELEGRAM_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        logger.warning("Telegram not configured; skipping notification")
        return

    for chunk in _split_message(message):
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": chunk},
            timeout=60,
        )
        response.raise_for_status()


def _split_message(message: str) -> list[str]:
    if len(message) <= TELEGRAM_MAX_LENGTH:
        return [message]

    chunks: list[str] = []
    remaining = message
    while remaining:
        if len(remaining) <= TELEGRAM_MAX_LENGTH:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n\n", 0, TELEGRAM_MAX_LENGTH)
        if cut < TELEGRAM_MAX_LENGTH // 2:
            cut = remaining.rfind("\n", 0, TELEGRAM_MAX_LENGTH)
        if cut < TELEGRAM_MAX_LENGTH // 2:
            cut = TELEGRAM_MAX_LENGTH
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks
