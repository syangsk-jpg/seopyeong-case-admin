"""
네이버 검색광고: 주 단위 클릭·노출 집계, 캠페인별, 키워드 상위 N.
완결된 과거 주 — 기본값 지난 5주(offset -1 … -5, 월~일, config.TZ 기준).

Run: python naver_weekly_views.py
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from integrations.naver_ads import _naver_get


def _iter_stat_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        inner = payload.get("data") or payload.get("results") or payload.get("items")
        if isinstance(inner, list):
            return [x for x in inner if isinstance(x, dict)]
    return []


def _row_imp(row: dict[str, Any]) -> int:
    return int(row.get("impCnt") or row.get("imp") or 0)


def _row_clk(row: dict[str, Any]) -> int:
    return int(row.get("clkCnt") or row.get("clk") or 0)


def _row_cost(row: dict[str, Any]) -> float:
    return float(row.get("salesAmt") or row.get("cost") or row.get("ccnt") or 0)


def _row_entity_id(row: dict[str, Any]) -> str | None:
    for k in ("nccKeywordId", "nccCampaignId", "nccAdgroupId", "id"):
        v = row.get(k)
        if v is not None and str(v):
            return str(v)
    return None


def _time_range(mon: date, sun: date) -> str:
    return json.dumps(
        {"since": mon.isoformat(), "until": sun.isoformat()},
        separators=(",", ":"),
    )


def _fetch_stats_chunk(part: list[str], tr: str, fields: str) -> dict[str, tuple[int, int, float]]:
    out: dict[str, tuple[int, int, float]] = {}
    r = _naver_get("/stats", {"ids": part, "fields": fields, "timeRange": tr})
    if r is None or not r.ok:
        return out
    try:
        body = r.json()
    except ValueError:
        return out
    for row in _iter_stat_rows(body):
        eid = _row_entity_id(row)
        if not eid:
            continue
        imp_prev, clk_prev, cost_prev = out.get(eid, (0, 0, 0.0))
        out[eid] = (
            imp_prev + _row_imp(row),
            clk_prev + _row_clk(row),
            cost_prev + _row_cost(row),
        )
    return out


def stats_aggregate_chunked(
    ids: list[str],
    mon: date,
    sun: date,
    *,
    fields: str,
    progress=None,
) -> dict[str, tuple[int, int, float]]:
    """GET /stats in parallel chunks → entity id → (노출, 클릭, 비용 salesAmt).

    66k+ 키워드를 순차 호출하면 수 분이 걸리므로 ThreadPoolExecutor로 병렬 처리한다.
    progress(done_chunks:int, total_chunks:int) 콜백으로 진행률을 알린다.
    """
    if not ids:
        return {}
    chunk = max(1, int(config.NAVER_STATS_IDS_PER_REQUEST))
    tr = _time_range(mon, sun)
    parts = [ids[i : i + chunk] for i in range(0, len(ids), chunk)]
    total = len(parts)
    merged: dict[str, tuple[int, int, float]] = {}

    workers = max(1, min(12, total))
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_fetch_stats_chunk, part, tr, fields) for part in parts]
        for fut in as_completed(futures):
            part_result = fut.result()
            for eid, (imp, clk, cost) in part_result.items():
                imp_prev, clk_prev, cost_prev = merged.get(eid, (0, 0, 0.0))
                merged[eid] = (imp_prev + imp, clk_prev + clk, cost_prev + cost)
            done += 1
            if callable(progress):
                try:
                    progress(done, total)
                except Exception:  # noqa: BLE001
                    pass
    return merged


def load_active_campaigns() -> list[dict[str, Any]]:
    r = _naver_get("/ncc/campaigns")
    if r is None or not r.ok:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    out: list[dict[str, Any]] = []
    for c in data:
        if not isinstance(c, dict) or c.get("delFlag"):
            continue
        cid = c.get("nccCampaignId")
        if not cid:
            continue
        out.append({"ncc_campaign_id": str(cid), "name": str(c.get("name") or "")})
    return out


def load_all_adgroups() -> list[dict[str, Any]]:
    r = _naver_get("/ncc/adgroups")
    if r is None or not r.ok:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def load_keywords_for_adgroup(adgroup_id: str) -> list[dict[str, Any]]:
    r = _naver_get("/ncc/keywords", {"nccAdgroupId": str(adgroup_id)})
    if r is None or not r.ok:
        return []
    try:
        data = r.json()
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def collect_keyword_inventory(campaign_labels: dict[str, str]) -> list[dict[str, str]]:
    """(ncc_keyword_id, keyword, campaign_name) — 삭제 플래그 제외."""

    def _keywords_for(pair: tuple[str, str]) -> tuple[str, str, list[dict[str, Any]]]:
        aid, cid = pair
        return aid, cid, load_keywords_for_adgroup(aid)

    adgroups = load_all_adgroups()
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    active_cids = set(campaign_labels.keys())

    pairs: list[tuple[str, str]] = []
    for ag in adgroups:
        if not isinstance(ag, dict) or ag.get("delFlag"):
            continue
        cid = str(ag.get("nccCampaignId") or "")
        if cid not in active_cids:
            continue
        ag_id = ag.get("nccAdgroupId")
        if not ag_id:
            continue
        pairs.append((str(ag_id), cid))

    if not pairs:
        return []

    w = max(1, min(14, len(pairs)))
    with ThreadPoolExecutor(max_workers=w) as ex:
        results = list(ex.map(_keywords_for, pairs))

    for _aid, cid, kwlist in results:
        cmap = campaign_labels.get(cid, "")
        for kw in kwlist:
            if not isinstance(kw, dict) or kw.get("delFlag"):
                continue
            kid = kw.get("nccKeywordId")
            kt = kw.get("keyword") or kw.get("name") or ""
            if not kid:
                continue
            ks = str(kid)
            if ks in seen:
                continue
            seen.add(ks)
            out.append(
                {
                    "ncc_keyword_id": ks,
                    "keyword": str(kt),
                    "ncc_campaign_id": cid,
                    "campaign_name": cmap,
                }
            )
    return out


def sync_week(
    mon: date,
    sun: date,
    *,
    campaigns: list[dict[str, Any]],
    inventory: list[dict[str, str]],
) -> dict[str, Any]:
    week_start_k = mon.isoformat()
    week_end_k = sun.isoformat()

    if not campaigns:
        return {"ok": False, "week_start": week_start_k, "error": "campaigns_empty"}

    camp_ids = [c["ncc_campaign_id"] for c in campaigns]
    camp_names = {c["ncc_campaign_id"]: c["name"] for c in campaigns}

    camp_metrics = stats_aggregate_chunked(
        camp_ids, mon, sun, fields='["impCnt","clkCnt","salesAmt"]'
    )
    total_impressions = sum(camp_metrics.get(cid, (0, 0, 0.0))[0] for cid in camp_ids)
    total_clicks = sum(camp_metrics.get(cid, (0, 0, 0.0))[1] for cid in camp_ids)
    total_cost = sum(camp_metrics.get(cid, (0, 0, 0.0))[2] for cid in camp_ids)

    campaign_rows: list[dict[str, Any]] = [
        {
            "ncc_campaign_id": cid,
            "campaign_name": camp_names.get(cid),
            "impressions": camp_metrics.get(cid, (0, 0, 0.0))[0],
            "clicks": camp_metrics.get(cid, (0, 0, 0.0))[1],
            "cost": camp_metrics.get(cid, (0, 0, 0.0))[2],
        }
        for cid in camp_ids
    ]

    kw_ids = [row["ncc_keyword_id"] for row in inventory]

    kw_metrics: dict[str, tuple[int, int]] = {}
    if kw_ids:
        kw_metrics = stats_aggregate_chunked(
            kw_ids, mon, sun, fields='["impCnt","clkCnt"]'
        )

    scored: list[tuple[int, int, dict[str, str]]] = [
        (
            kw_metrics.get(k["ncc_keyword_id"], (0, 0, 0.0))[0],
            kw_metrics.get(k["ncc_keyword_id"], (0, 0, 0.0))[1],
            k,
        )
        for k in inventory
    ]
    scored.sort(key=lambda x: (x[1], x[0]), reverse=True)  # 클릭 → 노출 순

    top_n = max(1, int(config.NAVER_WEEK_TOP_KEYWORDS))
    top_keywords: list[dict[str, Any]] = []
    for idx, (imp_val, clk_val, meta) in enumerate(scored[:top_n], start=1):
        top_keywords.append(
            {
                "rank": idx,
                "ncc_keyword_id": meta["ncc_keyword_id"],
                "keyword": meta["keyword"],
                "campaign_name": meta["campaign_name"],
                "impressions": imp_val,
                "clicks": clk_val,
            }
        )

    db.replace_naver_week_data(
        week_start_k,
        week_end_k,
        total_impressions,
        total_clicks,
        total_cost,
        campaign_rows,
        top_keywords,
    )
    return {
        "ok": True,
        "week_start": week_start_k,
        "week_end": week_end_k,
        "total_impressions": total_impressions,
        "total_clicks": total_clicks,
        "total_cost": total_cost,
        "campaigns": len(campaign_rows),
        "keywords_indexed": len(inventory),
    }


def cached_weeks() -> set[str]:
    """
    DB에 이미 저장된 주차 week_start 집합 — 재수집 생략 판단용.

    한 번 수집된 주는 SQLite에 남기고 API를 다시 부르지 않습니다.
    (강제 새로고침 시에만 skip_existing=False로 덮어씁니다.)
    """
    db.init_db()
    return {str(r["week_start"]) for r in db.fetch_naver_week_snapshots()}


def sync_naver_weekly_back(
    num_weeks: int | None = None,
    *,
    skip_existing: bool = True,
    progress=None,
) -> list[dict[str, Any]]:
    """
    과거 완결 주만: offset -1(직전 월~일)부터 num_weeks개.
    이번 주(진행 중)는 포함하지 않아 비교 단위가 맞습니다.

    skip_existing=True 이면 DB에 이미 유효한 스냅샷이 있는 주는 API를 다시 부르지 않습니다.
    progress(callable): progress(step:int, total:int, label:str) 형태로 진행 상황 콜백.
    """
    if num_weeks is None:
        num_weeks = max(1, int(config.NAVER_WEEKLY_BACK_WEEKS))
    db.init_db()
    if config.USE_MOCK_DATA:
        return [{"mock": True, "skipped": True}]
    if not (config.NAVER_CUSTOMER_ID and config.NAVER_API_KEY and config.NAVER_API_SECRET):
        return [{"error": "naver_credentials_missing"}]

    have = cached_weeks() if skip_existing else set()

    # 어떤 주를 실제로 받아야 하는지 먼저 계산
    targets: list[tuple[date, date]] = []
    skipped: list[dict[str, Any]] = []
    for k in range(1, num_weeks + 1):
        _, _, mon, sun = time_utils.calendar_week_bounds(-k)
        if mon.isoformat() in have:
            skipped.append({"ok": True, "week_start": mon.isoformat(), "cached": True})
        else:
            targets.append((mon, sun))

    results: list[dict[str, Any]] = list(skipped)
    if not targets:
        return results

    # 받을 주가 있을 때만 캠페인·키워드 인벤토리를 조회 (네트워크 절약)
    campaigns = load_active_campaigns()
    if not campaigns:
        results.append({"ok": False, "error": "campaigns_empty"})
        return results
    camp_name_map = {c["ncc_campaign_id"]: c["name"] for c in campaigns}
    inventory = collect_keyword_inventory(camp_name_map)

    total = len(targets)
    for idx, (mon, sun) in enumerate(targets, start=1):
        if callable(progress):
            try:
                progress(idx, total, f"{mon.isoformat()} ~ {sun.isoformat()}")
            except Exception:  # noqa: BLE001
                pass
        try:
            results.append(sync_week(mon, sun, campaigns=campaigns, inventory=inventory))
        except Exception as exc:  # noqa: BLE001
            results.append({"ok": False, "week_start": mon.isoformat(), "error": str(exc)})
    return results


def _load_campaigns_for_sync() -> list[dict[str, Any]]:
    db.init_db()
    if config.USE_MOCK_DATA:
        return []
    if not (config.NAVER_CUSTOMER_ID and config.NAVER_API_KEY and config.NAVER_API_SECRET):
        raise RuntimeError("naver_credentials_missing")
    campaigns = load_active_campaigns()
    if not campaigns:
        raise RuntimeError("campaigns_empty")
    return campaigns


def _load_inventory_for_campaigns(campaigns: list[dict[str, Any]]) -> list[dict[str, str]]:
    camp_name_map = {c["ncc_campaign_id"]: c["name"] for c in campaigns}
    return collect_keyword_inventory(camp_name_map)


def sync_naver_week_one(week_start: str) -> dict[str, Any]:
    """
    특정 월요일 시작 주차(YYYY-MM-DD)를 즉시 재조회해 덮어쓴다.
    UI에서 클릭 누락 주차를 빠르게 복구할 때 사용.
    """
    mon = date.fromisoformat(week_start)
    if mon.weekday() != 0:
        # 월~일 기준 강제 정렬
        mon = mon - timedelta(days=mon.weekday())
    sun = mon + timedelta(days=6)
    try:
        campaigns = _load_campaigns_for_sync()
        if config.USE_MOCK_DATA:
            return {"ok": True, "mock": True, "week_start": mon.isoformat(), "week_end": sun.isoformat()}
        # 빠른 경로: 이미 TOP N개가 채워진 주만 기존 키워드 ID로 재조회.
        # (DB 손상·부분 복구로 TOP이 모자라면 전체 inventory를 다시 모아 복구)
        top_n = max(1, int(config.NAVER_WEEK_TOP_KEYWORDS))
        existing = db.fetch_naver_week_keyword_top(mon.isoformat())
        if existing and len(existing) >= top_n:
            inventory = [
                {
                    "ncc_keyword_id": str(r["ncc_keyword_id"]),
                    "keyword": str(r["keyword"] or ""),
                    "ncc_campaign_id": "",
                    "campaign_name": str(r["campaign_name"] or ""),
                }
                for r in existing
                if r["ncc_keyword_id"]
            ]
        else:
            inventory = _load_inventory_for_campaigns(campaigns)
        return sync_week(mon, sun, campaigns=campaigns, inventory=inventory)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "week_start": mon.isoformat(), "week_end": sun.isoformat(), "error": str(exc)}


if __name__ == "__main__":
    out = sync_naver_weekly_back()
    for row in out:
        print(row)
