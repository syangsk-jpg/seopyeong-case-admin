"""
월간 집계 — 이미 저장된 주간 스냅샷(네이버·구글)을 "월요일이 속한 달" 기준으로 합산.
신규 API 호출 없이 기존 google_week_*/naver_week_* 테이블만 사용하는 근사치 월간 리포트.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd

import database as db
import google_weekly_report
import naver_weekly_report
import time_utils


def month_bounds(month_offset: int) -> tuple[date, date]:
    """month_offset: -1 = 지난 완료 달, -2 = 그 전 달, ... 0은 진행 중인 이번 달(비교용 아님)."""
    z = time_utils.tz()
    today = datetime.now(z).date()
    total_months = (today.year * 12 + (today.month - 1)) + month_offset
    year, month0 = divmod(total_months, 12)
    month = month0 + 1
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])
    return first_day, last_day


def weeks_in_month(month_start: date) -> list[date]:
    """해당 월에 월요일이 속하는 주들의 week_start(월요일) 목록."""
    last_day = date(month_start.year, month_start.month, calendar.monthrange(month_start.year, month_start.month)[1])
    days_to_monday = (-month_start.weekday()) % 7
    first_monday = month_start + timedelta(days=days_to_monday)
    mondays: list[date] = []
    cur = first_monday
    while cur <= last_day:
        mondays.append(cur)
        cur += timedelta(days=7)
    return mondays


def format_month_label(d: date) -> str:
    return f"{d.year}년 {d.month}월"


def report_meta_month_pair() -> tuple[date, date, date, date]:
    """지난 완료 달(-1) vs 그 전 달(-2)."""
    prev_start, prev_end = month_bounds(-2)
    curr_start, curr_end = month_bounds(-1)
    return prev_start, prev_end, curr_start, curr_end


def google_month_totals(month_start: date) -> dict[str, Any]:
    weeks = {d.isoformat() for d in weeks_in_month(month_start)}
    snaps = [dict(r) for r in db.fetch_google_week_snapshots() if str(r["week_start"]) in weeks]
    cost = sum(float(s.get("total_cost") or 0) for s in snaps)
    clicks = sum(float(s.get("total_clicks") or 0) for s in snaps)
    imp = sum(float(s.get("total_impressions") or 0) for s in snaps)
    conv = sum(float(s.get("total_conversions") or 0) for s in snaps)
    cpc = (cost / clicks) if clicks > 0 else 0.0
    return {
        "cost": cost,
        "clicks": clicks,
        "impressions": imp,
        "conversions": conv,
        "cpc": cpc,
        "weeks_covered": len(snaps),
        "weeks_expected": len(weeks),
        "has_data": bool(snaps),
    }


def naver_month_totals(month_start: date) -> dict[str, Any]:
    weeks = {d.isoformat() for d in weeks_in_month(month_start)}
    snaps = [dict(r) for r in db.fetch_naver_week_snapshots() if str(r["week_start"]) in weeks]
    cost = sum(float(s.get("total_cost") or 0) for s in snaps)
    clicks = sum(float(s.get("total_clicks") or 0) for s in snaps)
    imp = sum(float(s.get("total_impressions") or 0) for s in snaps)
    cpc = (cost / clicks) if clicks > 0 else 0.0
    return {
        "cost": cost,
        "clicks": clicks,
        "impressions": imp,
        "cpc": cpc,
        "weeks_covered": len(snaps),
        "weeks_expected": len(weeks),
        "has_data": bool(snaps),
    }


def google_month_campaigns(month_start: date, limit: int = 12) -> pd.DataFrame:
    weeks = weeks_in_month(month_start)
    frames = [google_weekly_report.campaigns_df(w.isoformat()) for w in weeks]
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame(columns=["campaign_name", "campaign_type", "cost", "clicks", "impressions", "cpc", "conversions", "cost_share_pct"])
    df = pd.concat(frames, ignore_index=True)
    grouped = df.groupby("campaign_name", as_index=False).agg(
        campaign_type=("campaign_type", "first"),
        cost=("cost", "sum"),
        clicks=("clicks", "sum"),
        impressions=("impressions", "sum"),
        conversions=("conversions", "sum"),
    )
    grouped["cpc"] = grouped.apply(lambda r: (float(r["cost"]) / r["clicks"]) if r["clicks"] > 0 else 0.0, axis=1)
    total_cost = float(grouped["cost"].sum())
    grouped["cost_share_pct"] = grouped["cost"].apply(
        lambda c: (100.0 * float(c) / total_cost) if total_cost > 0 else 0.0
    )
    return grouped.sort_values(["cost", "clicks"], ascending=False).head(limit).reset_index(drop=True)


def google_month_keywords_top(month_start: date, top_n: int = 30) -> pd.DataFrame:
    weeks = weeks_in_month(month_start)
    rows: list[dict[str, Any]] = []
    for w in weeks:
        rows.extend(dict(r) for r in db.fetch_google_week_keyword_top(w.isoformat()))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["clicks"] = pd.to_numeric(df.get("clicks", 0), errors="coerce").fillna(0).astype(int)
    df["impressions"] = pd.to_numeric(df.get("impressions", 0), errors="coerce").fillna(0).astype(int)
    df["cost"] = pd.to_numeric(df.get("cost", 0), errors="coerce").fillna(0.0)
    grouped = df.groupby(["keyword", "campaign_name"], as_index=False).agg(
        clicks=("clicks", "sum"),
        impressions=("impressions", "sum"),
        cost=("cost", "sum"),
    )
    grouped = grouped.sort_values(["clicks", "impressions"], ascending=[False, False]).head(top_n).reset_index(drop=True)
    grouped["rank"] = range(1, len(grouped) + 1)
    return grouped


def google_month_keywords_compare(month_curr: date, month_prev: date, top_n: int = 30) -> pd.DataFrame:
    cur = google_month_keywords_top(month_curr, top_n=top_n)
    if cur.empty:
        return pd.DataFrame()
    prev = google_month_keywords_top(month_prev, top_n=1000)
    prev_map: dict[str, int] = {}
    for _, row in prev.iterrows():
        prev_map[str(row["keyword"])] = int(row["clicks"])
    cur["clicks_curr"] = cur["clicks"].astype(int)
    cur["clicks_prev"] = cur["keyword"].astype(str).map(lambda k: prev_map.get(k, 0)).astype(int)
    cur["click_change"] = cur["clicks_curr"] - cur["clicks_prev"]

    def _chg_pct(row: pd.Series) -> float | None:
        prev_v = int(row["clicks_prev"])
        if prev_v <= 0:
            return None
        return round((int(row["clicks_curr"]) - prev_v) / prev_v * 100.0, 1)

    cur["click_change_pct"] = cur.apply(_chg_pct, axis=1)
    return cur
