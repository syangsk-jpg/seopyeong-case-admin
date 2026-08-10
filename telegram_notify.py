"""Send alert messages via Telegram Bot API."""
from __future__ import annotations

import json
from typing import Any

import requests

import config


def send_telegram(text: str, parse_mode: str | None = None) -> bool:
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload: dict[str, Any] = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": text[:4000],
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.ok
    except requests.RequestException:
        return False


def notify_admin_auth_failure(provider: str, detail: str) -> None:
    msg = f"[Auth/Token] {provider} 갱신 실패 또는 재인증 필요.\n{detail}"
    send_telegram(msg)
