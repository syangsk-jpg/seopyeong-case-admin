"""
Export merged metrics + alerts as JSON for external AI / agents / RAG tools.

Usage:
  from ai_export import build_analysis_payload, payload_to_json
  text = payload_to_json(build_analysis_payload(days=7))

CLI:
  python ai_export.py [--days 7] [--out data/ai_analysis_export.json]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import database as db
import merge
import time_utils

SCHEMA_VERSION = 1


def _df_records(df: Any) -> list[dict[str, Any]]:
    if df is None or getattr(df, "empty", True):
        return []
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _alerts_as_dicts(rows: list[Any], limit: int = 50) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows[:limit]:
        d = dict(r)
        out.append(
            {
                "created_at": d.get("created_at"),
                "alert_type": d.get("alert_type"),
                "message": d.get("message"),
                "payload_json": d.get("payload_json"),
            }
        )
    return out


def build_analysis_payload(days: int = 7) -> dict[str, Any]:
    """
    Single document your AI can ingest: totals, channel rollups, hourly merged series, alerts.
    All monetary fields are in account currency (e.g. KRW) as stored in SQLite.
    """
    db.init_db()
    start_s, end_s = time_utils.last_n_days_range(days)
    t0, _ = time_utils.today_range_iso()
    merged = merge.load_merged_range(start_s, end_s)
    merged_today = merged[merged["ts_hour"] >= t0] if not merged.empty else merged
    totals = merge.today_totals(merged_today)

    channel: dict[str, Any] = {}
    if not merged.empty:
        naver_sum = float(merged["naver_cost"].sum())
        google_sum = float(merged["google_cost"].sum())
        tc = naver_sum + google_sum
        sess = float(merged["sessions"].sum())
        channel = {
            "naver": {
                "cost_sum": naver_sum,
                "share_of_spend": (naver_sum / tc) if tc else 0.0,
            },
            "google": {
                "cost_sum": google_sum,
                "share_of_spend": (google_sum / tc) if tc else 0.0,
            },
            "blended_cost_per_session": (tc / sess) if sess else None,
        }

    alerts = _alerts_as_dicts(db.fetch_recent_alerts(80))

    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "timezone": time_utils.tz().key,
        "window": {"ts_hour_start": start_s, "ts_hour_end": end_s, "days": days},
        "today_totals": totals,
        "channel_summary": channel,
        "hourly_merged": _df_records(merged),
        "recent_alerts": alerts,
        "analysis_hints_for_llm": (
            "hourly_merged joins ad spend (naver_cost, google_cost, total_cost) with "
            "GA4/traffic (sessions, active_users, bounce_rate) on ts_hour. "
            "roas = total_conversion_value / total_cost when cost > 0. "
            "cost_per_session = total_cost / sessions when sessions > 0. "
            "Compare channel_summary.share_of_spend vs per-channel efficiency using hourly rows."
        ),
    }
    return payload


def payload_to_json(payload: dict[str, Any], indent: int = 2) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=indent)


def write_export(path: str | Path, days: int = 7) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = build_analysis_payload(days=days)
    p.write_text(payload_to_json(data), encoding="utf-8")
    return p


def main() -> None:
    ap = argparse.ArgumentParser(description="Export AI analysis JSON bundle")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--out", default="data/ai_analysis_export.json")
    args = ap.parse_args()
    out = write_export(args.out, days=args.days)
    print(out.resolve())


if __name__ == "__main__":
    main()
