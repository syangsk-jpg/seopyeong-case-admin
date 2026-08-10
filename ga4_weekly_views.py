"""
GA4 주간 스냅샷 수집 — 채널·이벤트·주요 페이지.
google_weekly_views.py와 동일한 캐시/백필 패턴.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import config
import database as db
import time_utils
from integrations.ga4_client import fetch_ga4_channel_week, fetch_ga4_events_week, fetch_ga4_pagepath_week

# GTM에서 만든 커스텀 전환 이벤트 — 대시보드가 별도로 추적하는 "주요 이벤트" 목록.
# (로톡 상담 클릭 / 카카오톡 문의 클릭 / 전문분야 클릭 / 변호사 소개 5초 체류 / AI 상담 전송 클릭)
KEY_EVENT_NAMES: list[str] = [
    "lawtalk_inquiry_click",
    "kakao_inquiry_click",
    "specialty_click",
    "lawyer_intro_dwell_5s",
    "ai_counselor_send_click",
]

# 대시보드가 별도로 페이지 조회수를 추적하는 주요 페이지 — 현재는 상담안내AI(챗봇) 페이지 1개.
# GA4 pagePath 는 도메인 뒤 경로만 내려오므로 "/AIcounselor" 형태로 정확히 일치시킵니다.
AI_COUNSELOR_PAGE_PATH = "/AIcounselor"
KEY_PAGE_PATHS: list[str] = [AI_COUNSELOR_PAGE_PATH]


def sync_week(mon: date, sun: date) -> dict[str, Any]:
    week_start_k = mon.isoformat()
    week_end_k = sun.isoformat()

    try:
        channels = fetch_ga4_channel_week(mon, sun)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "week_start": week_start_k, "error": str(exc)}

    try:
        events = fetch_ga4_events_week(mon, sun)
    except Exception:  # noqa: BLE001
        events = []

    try:
        pages = fetch_ga4_pagepath_week(mon, sun, page_paths=KEY_PAGE_PATHS)
    except Exception:  # noqa: BLE001
        pages = []

    db.replace_ga4_week_data(week_start_k, week_end_k, channels, events, pages=pages)
    return {
        "ok": True,
        "week_start": week_start_k,
        "week_end": week_end_k,
        "channels": len(channels),
        "events": len(events),
        "pages": len(pages),
    }


def cached_weeks() -> set[str]:
    """
    DB에 이미 유효한(세션·사용자 > 0) 스냅샷이 있는 주차 — 재수집 생략 판단용.
    빈 스냅샷(수집 전 0건)은 캐시로 보지 않아, 데이터가 생기면 다시 API를 부릅니다.
    주요 페이지(KEY_PAGE_PATHS) 조회수가 아직 없으면 캐시로 보지 않습니다
    (상담안내AI 페이지 등 신규 수집 항목 백필용).
    """
    db.init_db()
    out: set[str] = set()
    for r in db.fetch_ga4_week_snapshots():
        sess = int(r["total_sessions"] or 0)
        users = int(r["total_active_users"] or 0)
        if sess <= 0 and users <= 0:
            continue
        week_start = str(r["week_start"])
        page_rows = [dict(p) for p in db.fetch_ga4_week_page(week_start)]
        have_paths = {str(p.get("page_path") or "") for p in page_rows}
        # 추적 중인 주요 페이지가 DB에 없으면 재수집 대상
        if any(path not in have_paths for path in KEY_PAGE_PATHS):
            continue
        out.add(week_start)
    return out


def sync_ga4_weekly_back(
    num_weeks: int | None = None,
    *,
    skip_existing: bool = True,
    include_current: bool = False,
    progress=None,
) -> list[dict[str, Any]]:
    """
    과거 완결 주: offset -1(직전 월~일)부터 num_weeks개.
    include_current=True 이면 offset 0(이번 주, 월~일)도 함께 수집.

    skip_existing=True 이면 DB에 이미 유효한 스냅샷이 있는 주는 API를 다시 부르지 않습니다.
    progress(callable): progress(step:int, total:int, label:str) 형태로 진행 상황 콜백.
    """
    if num_weeks is None:
        num_weeks = max(1, int(getattr(config, "GA4_WEEKLY_BACK_WEEKS", 5) or 5))
    db.init_db()
    if config.USE_MOCK_DATA:
        return [{"mock": True, "skipped": True}]
    if not config.GA4_PROPERTY_ID:
        return [{"error": "ga4_property_id_missing"}]

    have = cached_weeks() if skip_existing else set()

    targets: list[tuple[date, date]] = []
    skipped: list[dict[str, Any]] = []
    offsets: list[int] = list(range(-1, -num_weeks - 1, -1))
    if include_current:
        offsets = [0] + offsets
    for k in offsets:
        _, _, mon, sun = time_utils.calendar_week_bounds(k)
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


def sync_ga4_week_one(week_start: str) -> dict[str, Any]:
    """특정 월요일 시작 주차(YYYY-MM-DD)를 즉시 재조회해 덮어쓴다."""
    mon = date.fromisoformat(week_start)
    if mon.weekday() != 0:
        mon = mon - timedelta(days=mon.weekday())
    sun = mon + timedelta(days=6)
    db.init_db()
    if config.USE_MOCK_DATA:
        return {"ok": True, "mock": True, "week_start": mon.isoformat(), "week_end": sun.isoformat()}
    if not config.GA4_PROPERTY_ID:
        return {"ok": False, "week_start": mon.isoformat(), "error": "ga4_property_id_missing"}
    try:
        return sync_week(mon, sun)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "week_start": mon.isoformat(), "week_end": sun.isoformat(), "error": str(exc)}


if __name__ == "__main__":
    out = sync_ga4_weekly_back(include_current=True)
    for row in out:
        print(row)
