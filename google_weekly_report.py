"""
Google Ads 주간 비교 리포트 (지난 완료 주 vs 그 전 주, 월~일).
DB 스냅샷(GAQL campaign/keyword_view) 기준 — naver_weekly_report.py와 동일한 형식.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

import database as db


def _snapshot_row(week_start: str) -> dict[str, Any] | None:
    for r in db.fetch_google_week_snapshots():
        if str(r["week_start"]) == week_start:
            return dict(r)
    return None


def week_totals(week_start: str) -> dict[str, float]:
    snap = _snapshot_row(week_start)
    if not snap:
        return {
            "cost": 0.0,
            "clicks": 0.0,
            "impressions": 0.0,
            "cpc": 0.0,
            "conversions": 0.0,
            "has_snapshot": False,
        }
    cost = float(snap.get("total_cost") or 0)
    clicks = float(snap.get("total_clicks") or 0)
    imp = float(snap.get("total_impressions") or 0)
    conv = float(snap.get("total_conversions") or 0)
    cpc = (cost / clicks) if clicks > 0 else 0.0
    return {
        "cost": cost,
        "clicks": clicks,
        "impressions": imp,
        "cpc": cpc,
        "conversions": conv,
        "has_snapshot": True,
        "synced_at": snap.get("synced_at"),
    }


def campaigns_df(week_start: str) -> pd.DataFrame:
    rows = [dict(r) for r in db.fetch_google_week_campaign_views(week_start)]
    cols = ["campaign_name", "campaign_type", "cost", "clicks", "impressions", "cpc", "conversions", "cost_share_pct"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["cost"] = pd.to_numeric(df.get("cost", 0), errors="coerce").fillna(0.0)
    df["clicks"] = pd.to_numeric(df.get("clicks", 0), errors="coerce").fillna(0).astype(int)
    df["impressions"] = pd.to_numeric(df.get("impressions", 0), errors="coerce").fillna(0).astype(int)
    df["conversions"] = pd.to_numeric(df.get("conversions", 0), errors="coerce").fillna(0.0)
    df["cpc"] = df.apply(lambda r: (float(r["cost"]) / r["clicks"]) if r["clicks"] > 0 else 0.0, axis=1)
    total_cost = float(df["cost"].sum())
    df["cost_share_pct"] = df["cost"].apply(
        lambda c: (100.0 * float(c) / total_cost) if total_cost > 0 else 0.0
    )
    return df.sort_values(["cost", "clicks"], ascending=False).reset_index(drop=True)


def _money_tag(delta: float) -> str:
    ad = abs(delta)
    if ad < 5000:
        return "소폭 절감" if delta < 0 else "소폭 증가"
    if ad < 50000:
        return "절감" if delta < 0 else "증가"
    return "대폭 절감" if delta < 0 else "대폭 증가"


def _count_tag(delta: float) -> str:
    if abs(delta) < 5:
        return "유지"
    return "감소" if delta < 0 else "증가"


def _cpc_tag(delta: float) -> str:
    if abs(delta) < 3:
        return "단가 유지"
    return "단가 상승 유지" if delta > 0 else "단가 하락"


def build_summary_table(prev_mon: date, prev_sun: date, curr_mon: date, curr_sun: date) -> pd.DataFrame:
    import time_utils

    p = week_totals(prev_mon.isoformat())
    c = week_totals(curr_mon.isoformat())

    def _row(metric: str, pv: float, cv: float, unit: str, tag_fn) -> dict[str, str]:
        delta = cv - pv
        p_txt = f"{int(round(pv)):,}{unit}"
        c_txt = f"{int(round(cv)):,}{unit}"
        d_txt = f"{int(round(delta)):+,}{unit} ({tag_fn(delta)})"
        return {
            "지표": metric,
            f"해당 주\n{time_utils.format_week_range(curr_mon, curr_sun)}": c_txt,
            f"그 전 주\n{time_utils.format_week_range(prev_mon, prev_sun)}": p_txt,
            "증감 분석": d_txt,
        }

    return pd.DataFrame(
        [
            _row("총 광고 비용", p["cost"], c["cost"], "원", _money_tag),
            _row("총 클릭수", p["clicks"], c["clicks"], "회", _count_tag),
            _row("평균 클릭비용 (CPC)", p["cpc"], c["cpc"], "원", _cpc_tag),
        ]
    )


def keyword_top_by_clicks(week_start: str, top_n: int = 20) -> pd.DataFrame:
    """해당 주 클릭 수 기준 상위 N 키워드."""
    raw = [dict(r) for r in db.fetch_google_week_keyword_top(week_start)]
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw)
    df["clicks"] = pd.to_numeric(df.get("clicks", 0), errors="coerce").fillna(0).astype(int)
    df["impressions"] = pd.to_numeric(df.get("impressions", 0), errors="coerce").fillna(0).astype(int)
    df["cost"] = pd.to_numeric(df.get("cost", 0), errors="coerce").fillna(0.0)
    df = df.sort_values(["clicks", "impressions"], ascending=[False, False]).head(top_n).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)
    return df


def keyword_clicks_compare(week_curr: str, week_prev: str, top_n: int = 20) -> pd.DataFrame:
    """해당 주 클릭 TOP N + 그 전 주 클릭 비교. 순위는 해당 주 클릭 수 기준."""
    cur = keyword_top_by_clicks(week_curr, top_n=top_n)
    if cur.empty:
        return pd.DataFrame()
    prev_raw = [dict(r) for r in db.fetch_google_week_keyword_top(week_prev)]
    prev_map: dict[str, int] = {}
    for row in prev_raw:
        key = str(row.get("keyword") or "")
        prev_map[key] = int(row.get("clicks") or 0)
    cur["clicks_curr"] = cur["clicks"].astype(int)
    cur["clicks_prev"] = cur["keyword"].astype(str).map(lambda k: prev_map.get(k, 0)).astype(int)
    cur["click_change"] = cur["clicks_curr"] - cur["clicks_prev"]

    def _chg_pct(row: pd.Series) -> float | None:
        prev = int(row["clicks_prev"])
        if prev <= 0:
            return None
        return round((int(row["clicks_curr"]) - prev) / prev * 100.0, 1)

    cur["click_change_pct"] = cur.apply(_chg_pct, axis=1)
    return cur


def campaign_weekly_compare(week_curr: str, week_prev: str) -> pd.DataFrame:
    """캠페인별 해당 주 vs 그 전 주 — 비용·클릭·CPC·노출 비교. 정렬: 해당 주 비용 내림차순."""
    curr_raw = {str(r["campaign_id"]): dict(r) for r in db.fetch_google_week_campaign_views(week_curr)}
    prev_raw = {str(r["campaign_id"]): dict(r) for r in db.fetch_google_week_campaign_views(week_prev)}
    all_ids = set(curr_raw.keys()) | set(prev_raw.keys())
    if not all_ids:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for cid in all_ids:
        cr = curr_raw.get(cid, {})
        pr = prev_raw.get(cid, {})
        name = str(cr.get("campaign_name") or pr.get("campaign_name") or "(이름 없음)")
        cost_c = float(cr.get("cost") or 0)
        cost_p = float(pr.get("cost") or 0)
        clk_c = int(cr.get("clicks") or 0)
        clk_p = int(pr.get("clicks") or 0)
        imp_c = int(cr.get("impressions") or 0)
        imp_p = int(pr.get("impressions") or 0)
        if cost_c <= 0 and cost_p <= 0 and clk_c <= 0 and clk_p <= 0:
            continue
        cpc_c = (cost_c / clk_c) if clk_c > 0 else 0.0
        cpc_p = (cost_p / clk_p) if clk_p > 0 else 0.0
        rows.append(
            {
                "campaign_name": name,
                "cost_curr": int(round(cost_c)),
                "cost_prev": int(round(cost_p)),
                "cost_change": int(round(cost_c - cost_p)),
                "clicks_curr": clk_c,
                "clicks_prev": clk_p,
                "click_change": clk_c - clk_p,
                "impressions_curr": imp_c,
                "impressions_prev": imp_p,
                "cpc_curr": int(round(cpc_c)),
                "cpc_prev": int(round(cpc_p)),
            }
        )

    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.sort_values(["cost_curr", "clicks_curr"], ascending=False).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)
    return df


def campaign_special_notes(week_start: str) -> list[str]:
    """선택 주 캠페인별 특이사항 자동 요약 (구글)."""
    cdf = campaigns_df(week_start)
    if cdf.empty:
        return ["캠페인별 데이터가 없어 특이사항을 계산하지 못했습니다."]

    notes: list[str] = []
    total_clicks = float(cdf["clicks"].sum())
    top = cdf.sort_values(["clicks", "impressions"], ascending=False).iloc[0]
    top_name = top["campaign_name"] or "(이름 없음)"
    top_clicks = float(top["clicks"])
    top_share = (100.0 * top_clicks / total_clicks) if total_clicks > 0 else 0.0
    if total_clicks > 0:
        notes.append(f"클릭 상위 캠페인: `{top_name}` {int(top_clicks):,}클릭 (전체의 {top_share:.1f}%).")
    else:
        notes.append("선택 주 캠페인 총 클릭이 0이라 집중도를 계산할 수 없습니다.")

    smart_zero = cdf[(cdf["campaign_type"] == "SMART") & (cdf["impressions"] <= 0)]
    if not smart_zero.empty:
        names = ", ".join(f"`{n}`" for n in smart_zero["campaign_name"].astype(str).tolist()[:3])
        notes.append(f"노출 0인 스마트 캠페인 존재: {names} — 사실상 미가동 상태입니다.")

    cdf_hi_imp = cdf[cdf["impressions"] >= 300].copy()
    if not cdf_hi_imp.empty:
        zero_clk = cdf_hi_imp[cdf_hi_imp["clicks"] <= 0]
        if not zero_clk.empty:
            z = zero_clk.iloc[0]
            notes.append(
                f"노출 300+인데 클릭 0인 캠페인 존재: `{z['campaign_name'] or '(이름 없음)'}` "
                f"(노출 {int(z['impressions']):,}). 소재/키워드 점검이 필요합니다."
            )

    if float(cdf["conversions"].sum()) <= 0:
        notes.append("선택 주 전환 수가 0으로 집계됐습니다 — 전환 추적 설정을 점검하세요.")

    return notes[:5]


def report_meta_week_pair() -> tuple[date, date, date, date]:
    """지난 완료 주(-1) vs 그 전 주(-2)."""
    import time_utils

    _, _, curr_mon, curr_sun = time_utils.calendar_week_bounds(-1)
    _, _, prev_mon, prev_sun = time_utils.calendar_week_bounds(-2)
    return prev_mon, prev_sun, curr_mon, curr_sun
