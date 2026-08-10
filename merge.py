"""
Date/time alignment: merge hourly ad metrics (per source) with site-wide traffic.

- `ts_hour` is normalized ISO local hour start in configured TZ, e.g. 2026-04-07T14:00:00+09:00
- Outer merge on `ts_hour` so hours with only ads or only traffic are preserved.
- Aggregates ad cost/clicks per hour across sources for spend-vs-traffic KPIs.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

import database as db


@dataclass
class MergedHourRow:
    ts_hour: str
    naver_cost: float
    google_cost: float
    total_cost: float
    total_clicks: int
    total_impressions: int
    active_users: float
    sessions: int
    bounce_rate: float | None
    cost_per_session: float | None  # efficiency: lower is better when sessions > 0


def rows_to_ad_df(rows: list[Any]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=[
                "ts_hour",
                "source",
                "cost",
                "clicks",
                "impressions",
                "cpc",
                "conversion_value",
                "fetched_at",
            ]
        )
    return pd.DataFrame([dict(r) for r in rows])


def rows_to_traffic_df(rows: list[Any]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame(
            columns=["ts_hour", "active_users", "sessions", "bounce_rate", "fetched_at"]
        )
    return pd.DataFrame([dict(r) for r in rows])


def merge_hourly_ad_traffic(ad_df: pd.DataFrame, traffic_df: pd.DataFrame) -> pd.DataFrame:
    """
    Full outer join on ts_hour. Pivot ad sources to columns for charting.
    """
    if ad_df.empty and traffic_df.empty:
        return pd.DataFrame()

    ad_pivot = None
    if not ad_df.empty:
        if "conversion_value" not in ad_df.columns:
            ad_df["conversion_value"] = 0.0
        cost_pivot = ad_df.pivot_table(
            index="ts_hour", columns="source", values="cost", aggfunc="sum", fill_value=0.0
        )
        clk_pivot = ad_df.pivot_table(
            index="ts_hour", columns="source", values="clicks", aggfunc="sum", fill_value=0.0
        )
        imp_pivot = ad_df.pivot_table(
            index="ts_hour", columns="source", values="impressions", aggfunc="sum", fill_value=0.0
        )
        ad_pivot = cost_pivot.reset_index()
        for col in ("naver", "google"):
            if col not in ad_pivot.columns:
                ad_pivot[col] = 0.0
        ad_pivot = ad_pivot.rename(columns={"naver": "naver_cost", "google": "google_cost"})
        clk = clk_pivot.reset_index()
        for col in ("naver", "google"):
            if col not in clk.columns:
                clk[col] = 0
        clk = clk.rename(columns={"naver": "naver_clicks", "google": "google_clicks"})
        imp = imp_pivot.reset_index()
        for col in ("naver", "google"):
            if col not in imp.columns:
                imp[col] = 0
        imp = imp.rename(columns={"naver": "naver_impressions", "google": "google_impressions"})
        clicks_sum = ad_df.groupby("ts_hour", as_index=False)["clicks"].sum()
        imp_sum = ad_df.groupby("ts_hour", as_index=False)["impressions"].sum()
        cv_sum = ad_df.groupby("ts_hour", as_index=False)["conversion_value"].sum()
        ad_pivot = ad_pivot.merge(clk, on="ts_hour", how="left")
        ad_pivot = ad_pivot.merge(imp, on="ts_hour", how="left")
        ad_pivot = ad_pivot.merge(clicks_sum, on="ts_hour", how="left")
        ad_pivot = ad_pivot.merge(imp_sum, on="ts_hour", how="left")
        ad_pivot = ad_pivot.merge(cv_sum, on="ts_hour", how="left")
        ad_pivot = ad_pivot.rename(
            columns={
                "clicks": "total_clicks",
                "impressions": "total_impressions",
                "conversion_value": "total_conversion_value",
            }
        )
    else:
        ad_pivot = pd.DataFrame(
            columns=[
                "ts_hour",
                "naver_cost",
                "google_cost",
                "naver_clicks",
                "google_clicks",
                "naver_impressions",
                "google_impressions",
                "total_clicks",
                "total_impressions",
                "total_conversion_value",
            ]
        )

    if traffic_df.empty:
        traffic_df = pd.DataFrame(columns=["ts_hour", "active_users", "sessions", "bounce_rate"])

    traffic_sub = traffic_df[["ts_hour", "active_users", "sessions", "bounce_rate"]].copy()

    merged = pd.merge(ad_pivot, traffic_sub, on="ts_hour", how="outer")
    merged = merged.sort_values("ts_hour").reset_index(drop=True)

    merged["naver_cost"] = merged["naver_cost"].fillna(0.0)
    merged["google_cost"] = merged["google_cost"].fillna(0.0)
    for c in ("naver_clicks", "google_clicks", "naver_impressions", "google_impressions"):
        if c not in merged.columns:
            merged[c] = 0
        merged[c] = merged[c].fillna(0).astype(int)
    merged["total_cost"] = merged["naver_cost"] + merged["google_cost"]
    merged["total_clicks"] = merged["total_clicks"].fillna(0).astype(int)
    merged["total_impressions"] = merged["total_impressions"].fillna(0).astype(int)
    if "total_conversion_value" not in merged.columns:
        merged["total_conversion_value"] = 0.0
    merged["total_conversion_value"] = merged["total_conversion_value"].fillna(0.0)
    merged["active_users"] = merged["active_users"].fillna(0.0)
    merged["sessions"] = merged["sessions"].fillna(0).astype(int)

    def _roas(row: pd.Series) -> float | None:
        c = row["total_cost"]
        if c and c > 0:
            return float(row["total_conversion_value"]) / float(c)
        return None

    merged["roas"] = merged.apply(_roas, axis=1)

    def _cps(row: pd.Series) -> float | None:
        s = row["sessions"]
        if s and s > 0:
            return float(row["total_cost"]) / float(s)
        return None

    merged["cost_per_session"] = merged.apply(_cps, axis=1)
    return merged


def load_merged_range(ts_hour_start: str, ts_hour_end: str) -> pd.DataFrame:
    ad_rows = db.fetch_ad_range(ts_hour_start, ts_hour_end)
    tr_rows = db.fetch_traffic_range(ts_hour_start, ts_hour_end)
    return merge_hourly_ad_traffic(rows_to_ad_df(ad_rows), rows_to_traffic_df(tr_rows))


def period_totals(merged_period: pd.DataFrame) -> dict[str, float]:
    """Sum cost, sessions, ROAS for any merged slice (e.g. today or selected week)."""
    if merged_period.empty:
        return {
            "total_cost": 0.0,
            "total_sessions": 0.0,
            "total_active_users": 0.0,
            "total_conversion_value": 0.0,
            "roas": 0.0,
        }
    tc = float(merged_period["total_cost"].sum())
    cv = (
        float(merged_period["total_conversion_value"].sum())
        if "total_conversion_value" in merged_period.columns
        else 0.0
    )
    return {
        "total_cost": tc,
        "total_sessions": float(merged_period["sessions"].sum()),
        "total_active_users": float(merged_period["active_users"].sum()),
        "total_conversion_value": cv,
        "roas": (cv / tc) if tc > 0 else 0.0,
    }


def today_totals(merged_today: pd.DataFrame) -> dict[str, float]:
    """Sum today's metrics from merged hourly frame (same as period_totals)."""
    return period_totals(merged_today)


def naver_week_totals(merged_week: pd.DataFrame) -> dict[str, float]:
    """Naver Search Ad-only aggregates for the selected week."""
    if merged_week.empty:
        return {
            "naver_cost": 0.0,
            "naver_clicks": 0.0,
            "naver_impressions": 0.0,
            "naver_cpc": 0.0,
        }
    cost = float(merged_week["naver_cost"].sum())
    clk = float(merged_week["naver_clicks"].sum()) if "naver_clicks" in merged_week.columns else 0.0
    imp = float(merged_week["naver_impressions"].sum()) if "naver_impressions" in merged_week.columns else 0.0
    cpc = (cost / clk) if clk else 0.0
    return {
        "naver_cost": cost,
        "naver_clicks": clk,
        "naver_impressions": imp,
        "naver_cpc": cpc,
    }


def google_week_totals(merged_week: pd.DataFrame) -> dict[str, float]:
    """Google Ads-only aggregates for the selected week (시간별 병합 테이블 기준)."""
    if merged_week.empty:
        return {
            "google_cost": 0.0,
            "google_clicks": 0.0,
            "google_impressions": 0.0,
            "google_cpc": 0.0,
        }
    cost = float(merged_week["google_cost"].sum())
    clk = float(merged_week["google_clicks"].sum()) if "google_clicks" in merged_week.columns else 0.0
    imp = float(merged_week["google_impressions"].sum()) if "google_impressions" in merged_week.columns else 0.0
    cpc = (cost / clk) if clk else 0.0
    return {
        "google_cost": cost,
        "google_clicks": clk,
        "google_impressions": imp,
        "google_cpc": cpc,
    }


def _naver_from_week_snapshot(week_start: str) -> dict[str, float] | None:
    """완결 주 네이버 스냅샷(/stats 캠페인 합계). 있으면 시간별 병합보다 정확."""
    for r in db.fetch_naver_week_snapshots():
        if str(r["week_start"]) != week_start:
            continue
        clicks = float(r["total_clicks"] or 0)
        imp = float(r["total_impressions"] or 0)
        cost = float(r["total_cost"] or 0)
        if clicks <= 0 and imp <= 0 and cost <= 0:
            return None
        return {
            "naver_cost": cost,
            "naver_clicks": clicks,
            "naver_impressions": imp,
            "naver_cpc": (cost / clicks) if clicks > 0 else 0.0,
        }
    return None


def _google_from_week_snapshot(week_start: str) -> dict[str, float] | None:
    """완결 주 구글 스냅샷(GAQL 합계). 있으면 시간별 병합보다 정확."""
    for r in db.fetch_google_week_snapshots():
        if str(r["week_start"]) != week_start:
            continue
        clicks = float(r["total_clicks"] or 0)
        imp = float(r["total_impressions"] or 0)
        cost = float(r["total_cost"] or 0)
        if clicks <= 0 and imp <= 0 and cost <= 0:
            return None
        return {
            "google_cost": cost,
            "google_clicks": clicks,
            "google_impressions": imp,
            "google_cpc": (cost / clicks) if clicks > 0 else 0.0,
        }
    return None


def week_ad_performance(merged_week: pd.DataFrame, week_start: str | None = None) -> dict[str, float]:
    """
    주간 통합 광고 성과.
    네이버·구글: `week_start`가 있으면 주간 스냅샷 우선
    (시간별 합산은 구간·동기화 범위에 따라 주가 잘리거나 과소 집계될 수 있음).
    """
    snap_g = _google_from_week_snapshot(week_start) if week_start else None
    if snap_g is not None:
        gw = snap_g
        google_source = "api_snapshot"
    else:
        gw = google_week_totals(merged_week)
        google_source = "hourly_fallback"
    snap_n = _naver_from_week_snapshot(week_start) if week_start else None
    if snap_n is not None:
        nw = snap_n
        naver_source = "api_snapshot"
    else:
        nw = naver_week_totals(merged_week)
        naver_source = "hourly_fallback"
    total_cost = nw["naver_cost"] + gw["google_cost"]
    total_clk = nw["naver_clicks"] + gw["google_clicks"]
    total_imp = nw["naver_impressions"] + gw["google_impressions"]
    blended_ctr = (100.0 * total_clk / total_imp) if total_imp > 0 else 0.0
    blended_cpc = (total_cost / total_clk) if total_clk > 0 else 0.0
    n_ctr = (100.0 * nw["naver_clicks"] / nw["naver_impressions"]) if nw["naver_impressions"] > 0 else 0.0
    g_ctr = (100.0 * gw["google_clicks"] / gw["google_impressions"]) if gw["google_impressions"] > 0 else 0.0
    out: dict[str, float] = {
        "total_cost": total_cost,
        "total_clicks": total_clk,
        "total_impressions": total_imp,
        "blended_ctr_pct": blended_ctr,
        "blended_cpc": blended_cpc,
        "naver_cost": nw["naver_cost"],
        "google_cost": gw["google_cost"],
        "naver_clicks": nw["naver_clicks"],
        "google_clicks": gw["google_clicks"],
        "naver_impressions": nw["naver_impressions"],
        "google_impressions": gw["google_impressions"],
        "naver_cpc": nw["naver_cpc"],
        "google_cpc": gw["google_cpc"],
        "naver_ctr_pct": n_ctr,
        "google_ctr_pct": g_ctr,
        "naver_source": naver_source,
        "google_source": google_source,
    }
    return out
