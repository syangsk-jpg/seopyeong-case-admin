"""
Naver Search Ad API — hourly spend/clicks/impressions.

Docs: https://naver.github.io/searchad-apidoc/

Uses API key + secret for signature; OAuth access token from refresh when configured.
If credentials are missing, returns [].
"""
from __future__ import annotations

import base64
import json
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests

import config
import database as db
import time_utils

NAVER_BASE = "https://api.searchad.naver.com"


def _secret_key_bytes(secret_key: str) -> bytes:
    """
    HMAC key must match official samples (python-sample/signaturehelper.py, php hash_hmac):
    UTF-8 bytes of the Secret Key string exactly as shown in API Manager — not Base64-decoded.
    """
    raw = (secret_key or "").strip()
    return raw.encode("utf-8") if raw else b""


def _sign(secret_key: str, timestamp: str, method: str, path: str) -> str:
    message = f"{timestamp}.{method}.{path}"
    key = _secret_key_bytes(secret_key)
    return base64.b64encode(
        hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")


def _naver_headers(method: str, path: str) -> dict[str, str]:
    ts = str(int(time.time() * 1000))
    sig = _sign(config.NAVER_API_SECRET or "", ts, method, path)
    return {
        "X-Timestamp": ts,
        "X-API-KEY": config.NAVER_API_KEY or "",
        "X-Customer": str(config.NAVER_CUSTOMER_ID or ""),
        "X-Signature": sig,
        "Content-Type": "application/json",
    }


def _naver_get(path: str, params: dict[str, Any] | None = None) -> requests.Response | None:
    """GET api.searchad.naver.com; refresh Bearer on first 401 if OAuth is configured."""
    params = params or {}
    headers = _naver_headers("GET", path)
    token = _get_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    url = NAVER_BASE + path
    try:
        r = requests.get(url, headers=headers, params=params, timeout=45)
        if r.status_code == 401:
            db.upsert_oauth_token("naver", None, None, None, None)
            token = _get_access_token()
            if token:
                headers = _naver_headers("GET", path)
                headers["Authorization"] = f"Bearer {token}"
                r = requests.get(url, headers=headers, params=params, timeout=45)
        if r.status_code == 401:
            import token_manager

            token_manager.handle_api_auth_error("naver", (r.text or "")[:500])
            return None
        return r
    except requests.RequestException:
        return None


def _get_access_token() -> str | None:
    """Prefer env refresh token; persist refreshed access token in SQLite."""
    row = db.get_token_row("naver")
    if row and row["access_token"] and row["expires_at"]:
        try:
            exp = datetime.fromisoformat(row["expires_at"].replace("Z", "+00:00"))
            if datetime.now(exp.tzinfo or ZoneInfo("UTC")) < exp - timedelta(minutes=2):
                return row["access_token"]
        except (ValueError, TypeError):
            pass

    if not (
        config.NAVER_API_KEY
        and config.NAVER_API_SECRET
        and config.NAVER_CUSTOMER_ID
        and config.NAVER_REFRESH_TOKEN
    ):
        return None

    # OAuth token endpoint (Search Ad)
    path = "/oauth2/token"
    url = NAVER_BASE + path
    body = {
        "grant_type": "refresh_token",
        "refresh_token": config.NAVER_REFRESH_TOKEN,
        "client_id": config.NAVER_API_KEY,
        "client_secret": config.NAVER_API_SECRET,
    }
    r = requests.post(url, data=body, timeout=30)
    r.raise_for_status()
    data = r.json()
    access = data.get("access_token")
    expires_in = int(data.get("expires_in", 3600))
    exp_at = datetime.utcnow() + timedelta(seconds=expires_in)
    db.upsert_oauth_token(
        "naver",
        access,
        data.get("refresh_token") or config.NAVER_REFRESH_TOKEN,
        exp_at.isoformat() + "Z",
        None,
    )
    return access


def fetch_naver_hourly(start: datetime, end: datetime) -> list[dict[str, Any]]:
    """
    Pull campaign or account-level stats and map to hourly rows keyed by time_utils.format_ts_hour.

    Note: Naver stat API granularity depends on report type; this uses a simplified
    daily split into hours when hourly breakdown is unavailable (proportional).
    For production, switch to stat-reports with `timeIncrement` hourly if your account supports it.
    """
    if config.USE_MOCK_DATA:
        from .mock_data import mock_ad_rows

        return [r for r in mock_ad_rows(start, end) if r["source"] == "naver"]

    if not (config.NAVER_CUSTOMER_ID and config.NAVER_API_KEY and config.NAVER_API_SECRET):
        return []

    campaigns_r = _naver_get("/ncc/campaigns")
    if campaigns_r is None or not campaigns_r.ok:
        return []
    try:
        campaigns = campaigns_r.json()
    except ValueError:
        return []
    if not isinstance(campaigns, list):
        return []
    cid_list = [
        str(c["nccCampaignId"])
        for c in campaigns
        if isinstance(c, dict) and c.get("nccCampaignId") and not c.get("delFlag")
    ]
    if not cid_list:
        return []

    time_range = json.dumps(
        {"since": start.date().isoformat(), "until": end.date().isoformat()},
        separators=(",", ":"),
    )
    stats_r = _naver_get(
        "/stats",
        {
            "ids": cid_list,
            "fields": '["impCnt","clkCnt","salesAmt"]',
            "timeRange": time_range,
        },
    )
    if stats_r is None or not stats_r.ok:
        return []
    try:
        payload = stats_r.json()
    except ValueError:
        return []

    rows: list[dict[str, Any]] = []
    z = time_utils.tz()
    # API returns structure varies; normalize common fields
    data = payload if isinstance(payload, list) else payload.get("data", payload.get("results", []))
    if not isinstance(data, list):
        data = [payload]

    total_imp = total_clk = total_cost = 0.0
    for item in data:
        if not isinstance(item, dict):
            continue
        total_imp += float(item.get("impCnt", item.get("imp", 0)) or 0)
        total_clk += float(item.get("clkCnt", item.get("clk", 0)) or 0)
        total_cost += float(item.get("salesAmt", item.get("cost", item.get("ccnt", 0))) or 0)

    # Distribute across hours in range (placeholder when API is daily-only)
    cur = time_utils.floor_to_hour(start.astimezone(z) if start.tzinfo else start.replace(tzinfo=z))
    end_f = time_utils.floor_to_hour(end.astimezone(z) if end.tzinfo else end.replace(tzinfo=z))
    hours: list[datetime] = []
    while cur <= end_f:
        hours.append(cur)
        cur = cur + timedelta(hours=1)
    n = max(len(hours), 1)
    per_imp = int(total_imp / n)
    per_clk = int(total_clk / n)
    per_cost = total_cost / n
    cpc = (per_cost / per_clk) if per_clk else None

    for h in hours:
        rows.append(
            {
                "ts_hour": time_utils.format_ts_hour(h),
                "source": "naver",
                "cost": per_cost,
                "clicks": per_clk,
                "impressions": per_imp,
                "cpc": cpc,
                "conversion_value": 0.0,
            }
        )
    return rows
