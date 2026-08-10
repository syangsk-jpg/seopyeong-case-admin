"""Optional hourly traffic from CSV when GA4 is unavailable.

Expected columns: ts_hour, active_users, sessions, bounce_rate
(ts_hour ISO local hour, same format as time_utils.format_ts_hour output)
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def load_traffic_csv(path: str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.is_file():
        return []
    df = pd.read_csv(p)
    need = {"ts_hour", "active_users", "sessions"}
    if not need.issubset(set(df.columns)):
        return []
    if "bounce_rate" not in df.columns:
        df["bounce_rate"] = None
    return df.to_dict("records")
