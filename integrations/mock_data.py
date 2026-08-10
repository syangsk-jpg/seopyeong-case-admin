"""Deterministic mock hourly rows for local demo."""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta
from typing import Any

import time_utils


def _walk_hours(start: datetime, end: datetime) -> list[datetime]:
    z = time_utils.tz()
    cur = time_utils.floor_to_hour(start.astimezone(z) if start.tzinfo else start.replace(tzinfo=z))
    end_f = time_utils.floor_to_hour(end.astimezone(z) if end.tzinfo else end.replace(tzinfo=z))
    out = []
    while cur <= end_f:
        out.append(cur)
        cur += timedelta(hours=1)
    return out


def mock_ad_rows(start: datetime, end: datetime) -> list[dict[str, Any]]:
    rnd = random.Random(42)
    rows: list[dict[str, Any]] = []
    for h in _walk_hours(start, end):
        ts = time_utils.format_ts_hour(h)
        for src, base in (("naver", 12000), ("google", 15000)):
            cost = base + rnd.randint(-2000, 4000) + int(500 * math.sin(h.hour))
            clicks = max(1, cost // 800 + rnd.randint(-5, 20))
            imp = clicks * (rnd.randint(8, 25))
            conv_val = float(cost) * rnd.uniform(2.0, 5.5)
            rows.append(
                {
                    "ts_hour": ts,
                    "source": src,
                    "cost": float(cost),
                    "clicks": int(clicks),
                    "impressions": int(imp),
                    "cpc": float(cost) / clicks,
                    "conversion_value": conv_val,
                }
            )
    return rows


def mock_traffic_rows(start: datetime, end: datetime) -> list[dict[str, Any]]:
    rnd = random.Random(7)
    rows: list[dict[str, Any]] = []
    for h in _walk_hours(start, end):
        ts = time_utils.format_ts_hour(h)
        sessions = max(0, rnd.randint(5, 80) + h.hour % 5)
        au = float(max(0, sessions - rnd.randint(0, 15)))
        br = rnd.uniform(0.25, 0.55)
        rows.append(
            {
                "ts_hour": ts,
                "active_users": au,
                "sessions": int(sessions),
                "bounce_rate": br,
            }
        )
    return rows
