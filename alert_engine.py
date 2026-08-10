"""
Smart alerts after sync:
1) Hourly spend vs baseline (median of same clock-hour over prior days) >= 50% spike
2) Spend > 0 but merged sessions == 0 for that hour (possible broken landing URLs)
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
import database as db
import merge
import telegram_notify as tg
import time_utils


def _hour_of_ts(ts_hour: str) -> int:
    try:
        return datetime.fromisoformat(ts_hour).hour
    except ValueError:
        return -1


def evaluate_and_alert() -> list[str]:
    db.init_db()
    start_s, end_s = time_utils.last_n_days_range(14)
    m = merge.load_merged_range(start_s, end_s)
    if m.empty:
        return []

    m = m.copy()
    m["hour_key"] = m["ts_hour"].map(_hour_of_ts)
    fired: list[str] = []

    # Latest complete hour: second-to-last row if last is in-progress, else last with any cost
    m_sorted = m.sort_values("ts_hour")
    target = None
    for idx in range(len(m_sorted) - 1, -1, -1):
        row = m_sorted.iloc[idx]
        if row["total_cost"] > 0 or row["sessions"] > 0:
            target = row
            break
    if target is None:
        return []
    target_ts = target["ts_hour"]
    hk = target["hour_key"]

    baseline = m_sorted[m_sorted["hour_key"] == hk]
    baseline = baseline[baseline["ts_hour"] != target_ts]
    if not baseline.empty and target["total_cost"] > 0:
        med = float(baseline["total_cost"].median())
        if med > 0 and float(target["total_cost"]) >= med * config.SPEND_SPIKE_RATIO:
            msg = (
                f"광고비 급증: {target_ts} 시간대 지출 {target['total_cost']:.0f}원, "
                f"동일 시간대 중앙값 대비 {float(target['total_cost'])/med:.1f}배 (임계 {config.SPEND_SPIKE_RATIO}배)"
            )
            _fire("spend_spike", msg, {"ts_hour": target_ts, "median": med, "cost": float(target["total_cost"])})
            fired.append(msg)

    if float(target["total_cost"]) > 0 and int(target["sessions"]) == 0:
        msg = (
            f"유입 이상: {target_ts} 에 광고비는 발생했으나 GA4 세션 0 — 랜딩/UTM/측정 설정을 확인하세요."
        )
        _fire("spend_zero_traffic", msg, {"ts_hour": target_ts, "cost": float(target["total_cost"])})
        fired.append(msg)

    return fired


def _fire(alert_type: str, message: str, payload: dict) -> None:
    db.insert_alert(alert_type, message, json.dumps(payload, ensure_ascii=False))
    tg.send_telegram(message)


if __name__ == "__main__":
    evaluate_and_alert()
