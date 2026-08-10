"""
Fetch Naver + Google Ads + GA4 and upsert SQLite.

Design:
- Past data stays in SQLite and is not re-fetched by default.
- Regular runs only refresh a short recent window (SYNC_INCREMENTAL_DAYS).
- Full lookback (SYNC_LOOKBACK_DAYS) runs when force=True or the DB has no hourly rows yet.

Run manually: `python sync_job.py`
Used by scheduler every hour.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
import database as db
import time_utils
from integrations.ga4_client import fetch_ga4_or_synthetic
from integrations.google_ads_client import fetch_google_ads_hourly
from integrations.naver_ads import fetch_naver_hourly


def _resolve_sync_days(days: int | None, *, force: bool) -> tuple[int, str]:
    """
    Returns (days, mode) where mode is 'force' | 'bootstrap' | 'incremental'.
    """
    full = max(1, int(config.SYNC_LOOKBACK_DAYS))
    incr = max(1, int(getattr(config, "SYNC_INCREMENTAL_DAYS", 2) or 2))
    if days is not None:
        return max(1, int(days)), "explicit"
    if force:
        return full, "force"
    db.init_db()
    if db.latest_ad_ts_hour() is None and db.latest_traffic_ts_hour() is None:
        # First collect: fill history once, then keep incremental only.
        return full, "bootstrap"
    return incr, "incremental"


def run_sync(days: int | None = None, *, force: bool = False) -> dict[str, int | str]:
    resolved_days, mode = _resolve_sync_days(days, force=force)
    db.init_db()
    z = time_utils.tz()
    end = datetime.now(z)
    start = end - timedelta(days=resolved_days)
    start_iso, end_iso = time_utils.hour_range_iso(start, end)

    ad_rows: list[dict] = []
    try:
        ad_rows.extend(fetch_naver_hourly(start, end))
    except Exception as e:  # noqa: BLE001
        _log_sync_error("naver", e)

    try:
        ad_rows.extend(fetch_google_ads_hourly(start, end))
    except Exception as e:  # noqa: BLE001
        _log_sync_error("google_ads", e)

    db.upsert_ad_hourly(ad_rows)

    try:
        tr = fetch_ga4_or_synthetic(start, end)
        db.upsert_traffic_hourly(tr)
    except Exception as e:  # noqa: BLE001
        _log_sync_error("ga4", e)

    return {
        "ad_points": len(ad_rows),
        "window_start": start_iso,
        "window_end": end_iso,
        "days": resolved_days,
        "mode": mode,
    }


def _log_sync_error(source: str, err: Exception) -> None:
    db.insert_alert(
        "sync_error",
        f"{source} 동기화 오류: {err}",
        json.dumps({"source": source, "error": str(err)}, ensure_ascii=False),
    )


if __name__ == "__main__":
    out = run_sync()
    print(out)
