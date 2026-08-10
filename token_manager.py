"""
Central hooks for OAuth failures: clear cache, notify admin.

Call `handle_api_auth_error` from integration code on 401/invalid_grant.
"""
from __future__ import annotations

import database as db
import telegram_notify as tg


def handle_api_auth_error(provider: str, detail: str) -> None:
    db.upsert_oauth_token(provider, None, None, None, None)
    tg.notify_admin_auth_failure(provider, detail)
