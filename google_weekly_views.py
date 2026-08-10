"""
Google Ads: 주 단위 캠페인·키워드 성과 집계 (완결된 월~일).
Run: python google_weekly_views.py
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
import database as db
import time_utils
from integrations.google_ads_client import _build_google_ads_client


def _customer_id() -> str:
    return (config.GOOGLE_ADS_CUSTOMER_ID or "").replace("-", "")


def fetch_campaign_week(mon: date, sun: date) -> list[dict[str, Any]]:
    """캠페인별 노출·클릭·비용·전환 (해당 월~일 합산)."""
    client = _build_google_ads_client()
    if client is None:
        return []
    ga = client.get_service("GoogleAdsService")
    query = f"""
        SELECT campaign.id, campaign.name, campaign.advertising_channel_type, campaign.campaign_budget,
               metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
        FROM campaign
        WHERE segments.date BETWEEN '{mon.isoformat()}' AND '{sun.isoformat()}'
    """
    agg: dict[str, dict[str, Any]] = {}
    response = ga.search_stream(customer_id=_customer_id(), query=query)
    for batch in response:
        for row in batch.results:
            cid = str(row.campaign.id)
            if cid not in agg:
                agg[cid] = {
                    "campaign_id": cid,
                    "campaign_name": row.campaign.name,
                    "campaign_type": row.campaign.advertising_channel_type.name,
                    "budget_resource_name": row.campaign.campaign_budget,
                    "impressions": 0,
                    "clicks": 0,
                    "cost": 0.0,
                    "conversions": 0.0,
                }
            agg[cid]["impressions"] += int(row.metrics.impressions or 0)
            agg[cid]["clicks"] += int(row.metrics.clicks or 0)
            agg[cid]["cost"] += float(row.metrics.cost_micros or 0) / 1_000_000
            agg[cid]["conversions"] += float(row.metrics.conversions or 0)
    return list(agg.values())


def fetch_keyword_week(mon: date, sun: date, top_n: int) -> list[dict[str, Any]]:
    """키워드별 노출·클릭·비용 — 클릭 수 기준 상위 top_n."""
    client = _build_google_ads_client()
    if client is None:
        return []
    ga = client.get_service("GoogleAdsService")
    query = f"""
        SELECT ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, ad_group_criterion.ad_group,
               campaign.name,
               metrics.impressions, metrics.clicks, metrics.cost_micros
        FROM keyword_view
        WHERE segments.date BETWEEN '{mon.isoformat()}' AND '{sun.isoformat()}'
    """
    agg: dict[str, dict[str, Any]] = {}
    response = ga.search_stream(customer_id=_customer_id(), query=query)
    for batch in response:
        for row in batch.results:
            kid = str(row.ad_group_criterion.criterion_id)
            if kid not in agg:
                agg[kid] = {
                    "criterion_id": kid,
                    "keyword": row.ad_group_criterion.keyword.text,
                    "campaign_name": row.campaign.name,
                    "ad_group_id": str(row.ad_group_criterion.ad_group).rsplit("/", 1)[-1],
                    "impressions": 0,
                    "clicks": 0,
                    "cost": 0.0,
                }
            agg[kid]["impressions"] += int(row.metrics.impressions or 0)
            agg[kid]["clicks"] += int(row.metrics.clicks or 0)
            agg[kid]["cost"] += float(row.metrics.cost_micros or 0) / 1_000_000

    scored = sorted(agg.values(), key=lambda r: (r["clicks"], r["impressions"]), reverse=True)
    top: list[dict[str, Any]] = []
    for idx, row in enumerate(scored[:top_n], start=1):
        top.append({"rank": idx, **row})
    return top


def fetch_search_terms_week(mon: date, sun: date, top_n: int = 50) -> list[dict[str, Any]]:
    """Actual phrases people typed before clicking a Google Search ad."""
    client = _build_google_ads_client()
    if client is None:
        return []
    ga = client.get_service("GoogleAdsService")
    query = f"""
        SELECT search_term_view.search_term, campaign.name, ad_group.name,
               metrics.impressions, metrics.clicks, metrics.cost_micros, metrics.conversions
        FROM search_term_view
        WHERE segments.date BETWEEN '{mon.isoformat()}' AND '{sun.isoformat()}'
    """
    agg: dict[tuple[str, str, str], dict[str, Any]] = {}
    try:
        response = ga.search_stream(customer_id=_customer_id(), query=query)
        for batch in response:
            for row in batch.results:
                term = str(row.search_term_view.search_term or "").strip()
                if not term:
                    continue
                key = (term, str(row.campaign.name or ""), str(row.ad_group.name or ""))
                item = agg.setdefault(
                    key,
                    {
                        "search_term": term,
                        "campaign_name": key[1],
                        "ad_group_name": key[2],
                        "impressions": 0,
                        "clicks": 0,
                        "cost": 0.0,
                        "conversions": 0.0,
                    },
                )
                item["impressions"] += int(row.metrics.impressions or 0)
                item["clicks"] += int(row.metrics.clicks or 0)
                item["cost"] += float(row.metrics.cost_micros or 0) / 1_000_000
                item["conversions"] += float(row.metrics.conversions or 0)
    except Exception:  # noqa: BLE001
        return []
    rows = sorted(
        agg.values(),
        key=lambda r: (r["clicks"], r["impressions"], r["conversions"]),
        reverse=True,
    )
    return rows[: max(1, int(top_n))]

def sync_week(mon: date, sun: date) -> dict[str, Any]:
    week_start_k = mon.isoformat()
    week_end_k = sun.isoformat()

    try:
        campaigns = fetch_campaign_week(mon, sun)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "week_start": week_start_k, "error": str(exc)}

    top_n = max(1, int(config.GOOGLE_WEEK_TOP_KEYWORDS))
    try:
        top_keywords = fetch_keyword_week(mon, sun, top_n)
    except Exception:  # noqa: BLE001
        top_keywords = []

    total_impressions = sum(c["impressions"] for c in campaigns)
    total_clicks = sum(c["clicks"] for c in campaigns)
    total_cost = sum(c["cost"] for c in campaigns)
    total_conversions = sum(c["conversions"] for c in campaigns)

    db.replace_google_week_data(
        week_start_k,
        week_end_k,
        total_impressions,
        total_clicks,
        total_cost,
        total_conversions,
        campaigns,
        top_keywords,
    )
    return {
        "ok": True,
        "week_start": week_start_k,
        "week_end": week_end_k,
        "total_impressions": total_impressions,
        "total_clicks": total_clicks,
        "total_cost": total_cost,
        "campaigns": len(campaigns),
        "keywords_indexed": len(top_keywords),
    }


def cached_weeks() -> set[str]:
    """
    DB에 이미 저장된 주차 week_start 집합 — 재수집 생략 판단용.

    한 번 수집된 주는 SQLite에 남기고 API를 다시 부르지 않습니다.
    (강제 새로고침 시에만 skip_existing=False로 덮어씁니다.)
    """
    db.init_db()
    return {str(r["week_start"]) for r in db.fetch_google_week_snapshots()}


def sync_google_weekly_back(
    num_weeks: int | None = None,
    *,
    skip_existing: bool = True,
    progress=None,
) -> list[dict[str, Any]]:
    """
    과거 완결 주만: offset -1(직전 월~일)부터 num_weeks개.

    skip_existing=True 이면 DB에 이미 유효한 스냅샷이 있는 주는 API를 다시 부르지 않습니다.
    progress(callable): progress(step:int, total:int, label:str) 형태로 진행 상황 콜백.
    """
    if num_weeks is None:
        num_weeks = max(1, int(config.GOOGLE_WEEKLY_BACK_WEEKS))
    db.init_db()
    if config.USE_MOCK_DATA:
        return [{"mock": True, "skipped": True}]
    if _build_google_ads_client() is None:
        return [{"error": "google_ads_credentials_missing"}]

    have = cached_weeks() if skip_existing else set()

    targets: list[tuple[date, date]] = []
    skipped: list[dict[str, Any]] = []
    for k in range(1, num_weeks + 1):
        _, _, mon, sun = time_utils.calendar_week_bounds(-k)
        if mon.isoformat() in have:
            skipped.append({"ok": True, "week_start": mon.isoformat(), "cached": True})
        else:
            targets.append((mon, sun))

    results: list[dict[str, Any]] = list(skipped)
    total = len(targets)
    for idx, (mon, sun) in enumerate(targets, start=1):
        if callable(progress):
            try:
                progress(idx, total, f"{mon.isoformat()} ~ {sun.isoformat()}")
            except Exception:  # noqa: BLE001
                pass
        try:
            results.append(sync_week(mon, sun))
        except Exception as exc:  # noqa: BLE001
            results.append({"ok": False, "week_start": mon.isoformat(), "error": str(exc)})
    return results


def sync_google_week_one(week_start: str) -> dict[str, Any]:
    """특정 월요일 시작 주차(YYYY-MM-DD)를 즉시 재조회해 덮어쓴다."""
    mon = date.fromisoformat(week_start)
    if mon.weekday() != 0:
        mon = mon - timedelta(days=mon.weekday())
    sun = mon + timedelta(days=6)
    db.init_db()
    if config.USE_MOCK_DATA:
        return {"ok": True, "mock": True, "week_start": mon.isoformat(), "week_end": sun.isoformat()}
    try:
        return sync_week(mon, sun)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "week_start": mon.isoformat(), "week_end": sun.isoformat(), "error": str(exc)}


if __name__ == "__main__":
    out = sync_google_weekly_back()
    for row in out:
        print(row)
