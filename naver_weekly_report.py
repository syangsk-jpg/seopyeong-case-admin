"""
네이버 검색광고 주간 비교 리포트 (지난 완료 주 vs 그 전 주, 월~일).
DB 스냅샷(캠페인 API /stats) 기준 — 실장님 확인 총계 형식.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

import database as db
import time_utils


def _fmt_md(d: date) -> str:
    return f"{d.month:02d}.{d.day:02d}"


def _fmt_period(d0: date, d1: date) -> str:
    return f"{_fmt_md(d0)} ~ {_fmt_md(d1)}"


def _fmt_period_title(d0: date, d1: date) -> str:
    return f"{_fmt_md(d0)}. ~ {_fmt_md(d1)}."


def _snapshot_row(week_start: str) -> dict[str, Any] | None:
    for r in db.fetch_naver_week_snapshots():
        if str(r["week_start"]) == week_start:
            return dict(r)
    return None


def week_totals(week_start: str) -> dict[str, float]:
    snap = _snapshot_row(week_start)
    if not snap:
        return {"cost": 0.0, "clicks": 0.0, "impressions": 0.0, "cpc": 0.0, "has_snapshot": False}
    cost = float(snap.get("total_cost") or 0)
    clicks = float(snap.get("total_clicks") or 0)
    imp = float(snap.get("total_impressions") or 0)
    cpc = (cost / clicks) if clicks > 0 else 0.0
    return {
        "cost": cost,
        "clicks": clicks,
        "impressions": imp,
        "cpc": cpc,
        "has_snapshot": True,
        "synced_at": snap.get("synced_at"),
    }


def campaigns_df(week_start: str) -> pd.DataFrame:
    rows = [dict(r) for r in db.fetch_naver_week_campaign_views(week_start)]
    if not rows:
        return pd.DataFrame(columns=["campaign_name", "cost", "clicks", "impressions", "cpc", "cost_share_pct"])
    df = pd.DataFrame(rows)
    df["cost"] = pd.to_numeric(df.get("cost", 0), errors="coerce").fillna(0.0)
    df["clicks"] = pd.to_numeric(df.get("clicks", 0), errors="coerce").fillna(0).astype(int)
    df["impressions"] = pd.to_numeric(df.get("impressions", 0), errors="coerce").fillna(0).astype(int)
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
    p = week_totals(prev_mon.isoformat())
    c = week_totals(curr_mon.isoformat())

    def _row(metric: str, pv: float, cv: float, fmt: str, tag_fn) -> dict[str, str]:
        delta = cv - pv
        if fmt == "money":
            p_txt = f"{int(round(pv)):,}원"
            c_txt = f"{int(round(cv)):,}원"
            d_txt = f"{int(round(delta)):+,}원 ({tag_fn(delta)})"
        elif fmt == "count":
            p_txt = f"{int(round(pv)):,}회"
            c_txt = f"{int(round(cv)):,}회"
            d_txt = f"{int(round(delta)):+,}회 ({tag_fn(delta)})"
        else:
            p_txt = f"{int(round(pv)):,}원"
            c_txt = f"{int(round(cv)):,}원"
            d_txt = f"{int(round(delta)):+,}원 ({tag_fn(delta)})"
        return {
            "지표 (실장님 확인 총계)": metric,
            f"해당 주\n{time_utils.format_week_range(curr_mon, curr_sun)}": c_txt,
            f"그 전 주\n{time_utils.format_week_range(prev_mon, prev_sun)}": p_txt,
            "증감 분석": d_txt,
        }

    return pd.DataFrame(
        [
            _row("총 광고 비용", p["cost"], c["cost"], "money", _money_tag),
            _row("총 클릭수", p["clicks"], c["clicks"], "count", _count_tag),
            _row("평균 클릭비용 (CPC)", p["cpc"], c["cpc"], "cpc", _cpc_tag),
        ]
    )


def _find_campaign(df: pd.DataFrame, *needles: str) -> dict[str, Any] | None:
    if df.empty or "campaign_name" not in df.columns:
        return None
    for needle in needles:
        m = df[df["campaign_name"].astype(str).str.contains(needle, case=False, na=False, regex=False)]
        if not m.empty:
            return m.iloc[0].to_dict()
    return None


def build_campaign_analysis(
    prev_mon: date,
    prev_sun: date,
    curr_mon: date,
    curr_sun: date,
) -> list[str]:
    """캠페인별 독주·저단가 유입 등 자동 코멘트 (스냅샷 캠페인 비용/클릭 기준)."""
    p_df = campaigns_df(prev_mon.isoformat())
    c_df = campaigns_df(curr_mon.isoformat())
    c_tot = week_totals(curr_mon.isoformat())
    p_tot = week_totals(prev_mon.isoformat())

    if c_df.empty:
        return [
            "캠페인별 데이터가 없습니다. 상단 「🔄 전체 동기화」를 실행해 주세요."
        ]
    if float(c_tot.get("cost") or 0) <= 0:
        return [
            "캠페인 비용(salesAmt)이 DB에 없습니다. 예전 수집본일 수 있어 **두 주차 모두 재조회** 후 다시 확인하세요."
        ]

    lines: list[str] = []
    total_clk = float(c_tot["clicks"]) or 1.0
    top = c_df.iloc[0]
    top_name = str(top.get("campaign_name") or "(이름 없음)")
    top_share = float(top.get("cost_share_pct") or 0)
    top_cost = float(top.get("cost") or 0)
    top_clk = int(top.get("clicks") or 0)
    top_cpc = float(top.get("cpc") or 0)

    lines.append(
        f"해당 주 예산이 **{int(c_tot['cost']):,}원**대로 집행된 가운데, "
        f"**'{top_name}'** 캠페인이 비용 **{top_share:.1f}%**를 차지했습니다."
    )

    if top_share >= 35:
        prev_top = _find_campaign(p_df, top_name[:12]) if not p_df.empty else None
        prev_cost = float(prev_top.get("cost") or 0) if prev_top else 0.0
        prev_clk = int(prev_top.get("clicks") or 0) if prev_top else 0
        prev_cpc = float(prev_top.get("cpc") or 0) if prev_top else 0.0
        lines.append(
            f"**'{top_name}'**의 압도적 지출 (약 **{top_share:.0f}%** 점유) — "
            f"클릭 **{top_clk:,}회**, CPC **{top_cpc:,.0f}원**, 비용 **{top_cost:,.0f}원**. "
            f"그 전 주(CPC **{prev_cpc:,.0f}원** / **{prev_cost:,.0f}원**) 대비 클릭 **{top_clk - prev_clk:+,}회**, "
            f"단가 **{top_cpc - prev_cpc:+,.0f}원**, 비용 **{top_cost - prev_cost:+,.0f}원**."
        )

    for needles, label in (
        (("가사", "파워링크"), "파워링크·가사소송"),
        (("음주",), "음주운전"),
        (("변호사", "지역"), "변호사·지역"),
    ):
        row = _find_campaign(c_df, *needles)
        if not row:
            continue
        nm = str(row["campaign_name"])
        if nm == top_name and top_share >= 35:
            continue
        clk = int(row["clicks"])
        cost = float(row["cost"])
        cpc = float(row["cpc"])
        share_clk = 100.0 * clk / total_clk if total_clk else 0
        prev_row = _find_campaign(p_df, *needles)
        p_clk = int(prev_row.get("clicks") or 0) if prev_row else 0
        p_cost = float(prev_row.get("cost") or 0) if prev_row else 0.0
        lines.append(
            f"**{label} ({nm})** — 클릭 **{clk:,}회** / 비용 **{cost:,.0f}원** (CPC **{cpc:,.0f}원**, "
            f"전체 클릭 약 **{share_clk:.1f}%**). 그 전 주 **{p_clk:,}회** / **{p_cost:,.0f}원**."
        )

    # 저단가 대량 유입: 클릭 비중 높고 CPC 낮음
    for _, row in c_df.iterrows():
        clk = int(row["clicks"])
        if clk < 200:
            continue
        cpc = float(row["cpc"])
        if cpc <= 250 and clk >= 500:
            share_clk = 100.0 * clk / total_clk
            nm = str(row["campaign_name"])
            lines.append(
                f"**{nm}** (저단가 유입) — 클릭 **{clk:,}회** / 비용 **{float(row['cost']):,.0f}원** "
                f"(CPC **{cpc:,.0f}원**, 전체 클릭의 약 **{share_clk:.1f}%**)."
            )
            break

    # 중간 CPC 안정 캠페인 (형사 등)
    mid = c_df[(c_df["clicks"] >= 20) & (c_df["cpc"] >= 800) & (c_df["cpc"] <= 3000)]
    if not mid.empty:
        r = mid.iloc[0]
        nm = str(r["campaign_name"])
        p_row = _find_campaign(p_df, nm[:8])
        p_clk = int(p_row.get("clicks") or 0) if p_row else 0
        p_cost = float(p_row.get("cost") or 0) if p_row else 0.0
        lines.append(
            f"**{nm}** — 클릭 **{int(r['clicks']):,}회** / 비용 **{float(r['cost']):,.0f}원** "
            f"(CPC **{float(r['cpc']):,.0f}원**). 그 전 주 {p_clk:,}회 / {p_cost:,.0f}원 대비 형사·전문 유입 믹스 참고."
        )

    if float(p_tot.get("cost") or 0) > 0:
        d_cost = float(c_tot["cost"]) - float(p_tot["cost"])
        d_clk = float(c_tot["clicks"]) - float(p_tot["clicks"])
        lines.append(
            f"두 주 합산 참고: 비용 {d_cost:+,.0f}원, 클릭 {d_clk:+,.0f}회 변화 (API 캠페인 합계 기준)."
        )

    return lines


def keyword_top_by_clicks(week_start: str, top_n: int = 20) -> pd.DataFrame:
    """해당 주 클릭 수 기준 상위 N 키워드."""
    raw = [dict(r) for r in db.fetch_naver_week_keyword_top(week_start)]
    if not raw:
        return pd.DataFrame()
    df = pd.DataFrame(raw)
    df["clicks"] = pd.to_numeric(df.get("clicks", 0), errors="coerce").fillna(0).astype(int)
    df["impressions"] = pd.to_numeric(df.get("impressions", 0), errors="coerce").fillna(0).astype(int)
    df = df.sort_values(["clicks", "impressions"], ascending=[False, False]).head(top_n).reset_index(drop=True)
    df["rank"] = range(1, len(df) + 1)
    return df


def keyword_clicks_compare(week_curr: str, week_prev: str, top_n: int = 20) -> pd.DataFrame:
    """
    해당 주 클릭 TOP N + 그 전 주 클릭 비교.
    순위는 **해당 주 클릭 수** 기준.
    """
    cur = keyword_top_by_clicks(week_curr, top_n=top_n)
    if cur.empty:
        return pd.DataFrame()
    prev_raw = [dict(r) for r in db.fetch_naver_week_keyword_top(week_prev)]
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
    """
    캠페인별 해당 주 vs 그 전 주 — 비용·클릭·CPC·노출 비교.
    정렬: 해당 주 비용 내림차순.
    """
    curr_raw = {str(r["ncc_campaign_id"]): dict(r) for r in db.fetch_naver_week_campaign_views(week_curr)}
    prev_raw = {str(r["ncc_campaign_id"]): dict(r) for r in db.fetch_naver_week_campaign_views(week_prev)}
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


def weeks_needing_requery() -> list[dict[str, Any]]:
    """클릭·비용이 0이거나 스냅샷이 없는 주차."""
    issues: list[dict[str, Any]] = []
    have = {str(r["week_start"]): dict(r) for r in db.fetch_naver_week_snapshots()}
    for off in range(-1, -9, -1):
        _, _, mon, _sun = time_utils.calendar_week_bounds(off)
        ws = mon.isoformat()
        row = have.get(ws)
        if not row:
            issues.append({"week_start": ws, "reason": "스냅샷 없음"})
            continue
        if int(row.get("total_clicks") or 0) <= 0:
            issues.append({"week_start": ws, "reason": "클릭 0"})
        if float(row.get("total_cost") or 0) <= 0:
            issues.append({"week_start": ws, "reason": "비용 0 (재수집 필요)"})
    return issues


def report_meta_week_pair() -> tuple[date, date, date, date, str]:
    """지난 완료 주(-1) vs 그 전 주(-2), 제목용 기간 문자열."""
    _, _, curr_mon, curr_sun = time_utils.calendar_week_bounds(-1)
    _, _, prev_mon, prev_sun = time_utils.calendar_week_bounds(-2)
    title_span = _fmt_period_title(prev_mon, curr_sun)
    return prev_mon, prev_sun, curr_mon, curr_sun, title_span
