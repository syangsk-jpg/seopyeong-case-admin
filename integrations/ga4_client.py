"""
GA4 Data API — hourly activeUsers/sessions + weekly channel/event/page.

Requires: pip install google-analytics-data (when not using USE_MOCK_DATA).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import config
import time_utils


def _ga4_client_and_property():
    """(client, property_id) or (None, None) if unavailable."""
    if config.USE_MOCK_DATA or not config.GA4_PROPERTY_ID:
        return None, None
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
    except ImportError:
        return None, None
    return BetaAnalyticsDataClient(), f"properties/{config.GA4_PROPERTY_ID}"


def fetch_ga4_hourly(start: datetime, end: datetime) -> list[dict[str, Any]]:
    if config.USE_MOCK_DATA:
        from .mock_data import mock_traffic_rows

        return mock_traffic_rows(start, end)

    if not config.GA4_PROPERTY_ID:
        return []

    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest
    except ImportError:
        return []

    client = BetaAnalyticsDataClient()
    prop = f"properties/{config.GA4_PROPERTY_ID}"
    z = time_utils.tz()
    start_d = start.astimezone(z).date()
    end_d = end.astimezone(z).date()

    request = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=start_d.isoformat(), end_date=end_d.isoformat())],
        dimensions=[
            Dimension(name="date"),
            Dimension(name="hour"),
        ],
        metrics=[
            Metric(name="activeUsers"),
            Metric(name="sessions"),
            Metric(name="bounceRate"),
        ],
    )

    try:
        resp = client.run_report(request)
    except Exception:  # noqa: BLE001
        return []

    rows: list[dict[str, Any]] = []
    for r in resp.rows:
        ds = r.dimension_values[0].value
        hour = int(r.dimension_values[1].value)
        dt = datetime.strptime(ds, "%Y%m%d").replace(hour=hour, tzinfo=z)
        au = float(r.metric_values[0].value or 0)
        sess = int(float(r.metric_values[1].value or 0))
        br = float(r.metric_values[2].value or 0) if r.metric_values[2].value else None
        rows.append(
            {
                "ts_hour": time_utils.format_ts_hour(dt),
                "active_users": au,
                "sessions": sess,
                "bounce_rate": br,
            }
        )

    return sorted(rows, key=lambda x: x["ts_hour"])


def fetch_ga4_or_synthetic(start: datetime, end: datetime) -> list[dict[str, Any]]:
    got = fetch_ga4_hourly(start, end)
    if got:
        return got
    if config.TRAFFIC_LOG_CSV:
        from .traffic_csv import load_traffic_csv

        rows = load_traffic_csv(config.TRAFFIC_LOG_CSV)
        start_s = time_utils.format_ts_hour(time_utils.floor_to_hour(start))
        end_s = time_utils.format_ts_hour(time_utils.floor_to_hour(end))
        return [r for r in rows if start_s <= str(r.get("ts_hour", "")) <= end_s]
    return []


def fetch_ga4_channel_week(mon: date, sun: date) -> list[dict[str, Any]]:
    """주간 세션 기본 채널 그룹별 sessions / activeUsers / engagedSessions."""
    client, prop = _ga4_client_and_property()
    if client is None or prop is None:
        return []

    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=mon.isoformat(), end_date=sun.isoformat())],
        dimensions=[Dimension(name="sessionDefaultChannelGroup")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="activeUsers"),
            Metric(name="engagedSessions"),
        ],
    )
    try:
        resp = client.run_report(request)
    except Exception:  # noqa: BLE001
        return []

    rows: list[dict[str, Any]] = []
    for r in resp.rows:
        rows.append(
            {
                "channel": r.dimension_values[0].value or "Unassigned",
                "sessions": int(float(r.metric_values[0].value or 0)),
                "active_users": int(float(r.metric_values[1].value or 0)),
                "engaged_sessions": int(float(r.metric_values[2].value or 0)),
            }
        )
    return sorted(rows, key=lambda x: x["sessions"], reverse=True)


def fetch_ga4_unassigned_detail_week(mon: date, sun: date) -> list[dict[str, Any]]:
    """Return acquisition details for GA4 sessions classified as Unassigned."""
    client, prop = _ga4_client_and_property()
    if client is None or prop is None:
        return []

    from google.analytics.data_v1beta.types import (
        DateRange, Dimension, Filter, FilterExpression, Metric, RunReportRequest,
    )

    request = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=mon.isoformat(), end_date=sun.isoformat())],
        dimensions=[
            Dimension(name="sessionSource"),
            Dimension(name="sessionMedium"),
            Dimension(name="sessionCampaignName"),
            Dimension(name="landingPagePlusQueryString"),
        ],
        metrics=[Metric(name="sessions"), Metric(name="activeUsers")],
        dimension_filter=FilterExpression(
            filter=Filter(
                field_name="sessionDefaultChannelGroup",
                string_filter=Filter.StringFilter(
                    match_type=Filter.StringFilter.MatchType.EXACT,
                    value="Unassigned",
                ),
            )
        ),
        limit=500,
    )
    try:
        resp = client.run_report(request)
    except Exception:  # noqa: BLE001
        return []

    rows: list[dict[str, Any]] = []
    for r in resp.rows:
        rows.append(
            {
                "source": r.dimension_values[0].value or "(not set)",
                "medium": r.dimension_values[1].value or "(not set)",
                "campaign": r.dimension_values[2].value or "(not set)",
                "landing_page": r.dimension_values[3].value or "(not set)",
                "sessions": int(float(r.metric_values[0].value or 0)),
                "active_users": int(float(r.metric_values[1].value or 0)),
            }
        )
    return sorted(rows, key=lambda x: x["sessions"], reverse=True)

def fetch_ga4_events_week(mon: date, sun: date) -> list[dict[str, Any]]:
    """주간 eventName별 eventCount."""
    client, prop = _ga4_client_and_property()
    if client is None or prop is None:
        return []

    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=mon.isoformat(), end_date=sun.isoformat())],
        dimensions=[Dimension(name="eventName")],
        metrics=[Metric(name="eventCount")],
        limit=250,
    )
    try:
        resp = client.run_report(request)
    except Exception:  # noqa: BLE001
        return []

    rows: list[dict[str, Any]] = []
    for r in resp.rows:
        rows.append(
            {
                "event_name": r.dimension_values[0].value or "",
                "event_count": int(float(r.metric_values[0].value or 0)),
            }
        )
    return sorted(rows, key=lambda x: x["event_count"], reverse=True)


def fetch_ga4_pagepath_week(
    mon: date,
    sun: date,
    *,
    page_paths: list[str] | None = None,
) -> list[dict[str, Any]]:
    """주간 pagePath별 조회수·활성 사용자·평균 참여 시간(초).

    page_paths가 있으면 해당 경로만 남깁니다(정확한 문자열 일치).
    """
    client, prop = _ga4_client_and_property()
    if client is None or prop is None:
        return []

    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=mon.isoformat(), end_date=sun.isoformat())],
        dimensions=[Dimension(name="pagePath")],
        metrics=[
            Metric(name="screenPageViews"),
            Metric(name="activeUsers"),
            Metric(name="userEngagementDuration"),
        ],
        limit=10000,
    )
    try:
        resp = client.run_report(request)
    except Exception:  # noqa: BLE001
        return []

    want = set(page_paths) if page_paths else None
    rows: list[dict[str, Any]] = []
    for r in resp.rows:
        path = r.dimension_values[0].value or ""
        if want is not None and path not in want:
            continue
        views = int(float(r.metric_values[0].value or 0))
        users = int(float(r.metric_values[1].value or 0))
        # userEngagementDuration = 총 초. 조회당 평균으로 환산.
        eng_total = float(r.metric_values[2].value or 0)
        avg_eng = (eng_total / views) if views > 0 else 0.0
        rows.append(
            {
                "page_path": path,
                "views": views,
                "active_users": users,
                "avg_engagement_sec": avg_eng,
            }
        )
    # 요청한 경로가 응답에 없으면 0행으로 채워 캐시 판정·UI가 경로를 인식하게 함
    if want is not None:
        have = {r["page_path"] for r in rows}
        for path in page_paths or []:
            if path not in have:
                rows.append(
                    {
                        "page_path": path,
                        "views": 0,
                        "active_users": 0,
                        "avg_engagement_sec": 0.0,
                    }
                )
    return sorted(rows, key=lambda x: x["views"], reverse=True)


def fetch_ga4_content_week(mon: date, sun: date) -> list[dict[str, Any]]:
    """Return the pages and article titles people viewed during the week."""
    client, prop = _ga4_client_and_property()
    if client is None or prop is None:
        return []

    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=mon.isoformat(), end_date=sun.isoformat())],
        dimensions=[Dimension(name="pageTitle"), Dimension(name="pagePathPlusQueryString")],
        metrics=[
            Metric(name="screenPageViews"),
            Metric(name="activeUsers"),
            Metric(name="userEngagementDuration"),
        ],
        limit=1000,
    )
    try:
        resp = client.run_report(request)
    except Exception:  # noqa: BLE001
        return []

    rows: list[dict[str, Any]] = []
    for r in resp.rows:
        views = int(float(r.metric_values[0].value or 0))
        engagement = float(r.metric_values[2].value or 0)
        rows.append(
            {
                "page_title": r.dimension_values[0].value or "(제목 없음)",
                "page_path": r.dimension_values[1].value or "/",
                "views": views,
                "active_users": int(float(r.metric_values[1].value or 0)),
                "avg_engagement_sec": (engagement / views) if views else 0.0,
            }
        )
    return sorted(rows, key=lambda x: x["views"], reverse=True)


def fetch_ga4_search_source_week(mon: date, sun: date) -> list[dict[str, Any]]:
    """Return Google/Naver search sessions split by source and landing page."""
    client, prop = _ga4_client_and_property()
    if client is None or prop is None:
        return []

    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=mon.isoformat(), end_date=sun.isoformat())],
        dimensions=[
            Dimension(name="sessionSource"),
            Dimension(name="sessionMedium"),
            Dimension(name="sessionDefaultChannelGroup"),
            Dimension(name="landingPagePlusQueryString"),
        ],
        metrics=[Metric(name="sessions"), Metric(name="activeUsers")],
        limit=1000,
    )
    try:
        resp = client.run_report(request)
    except Exception:  # noqa: BLE001
        return []

    rows: list[dict[str, Any]] = []
    for r in resp.rows:
        source = (r.dimension_values[0].value or "(not set)").lower()
        if "google" in source:
            engine = "구글"
        elif "naver" in source:
            engine = "네이버"
        else:
            continue
        channel = r.dimension_values[2].value or "Unassigned"
        if channel == "Organic Search":
            search_type = "자연 검색"
        elif channel in {"Paid Search", "Cross-network"}:
            search_type = "검색·통합 광고"
        else:
            search_type = "기타"
        rows.append(
            {
                "search_engine": engine,
                "search_type": search_type,
                "source": source,
                "medium": r.dimension_values[1].value or "(not set)",
                "channel": channel,
                "landing_page": r.dimension_values[3].value or "/",
                "sessions": int(float(r.metric_values[0].value or 0)),
                "active_users": int(float(r.metric_values[1].value or 0)),
            }
        )
    return sorted(rows, key=lambda x: x["sessions"], reverse=True)

def fetch_ga4_daily_traffic(start: date, end: date) -> list[dict[str, Any]]:
    """Return daily visitors, sessions, and page views for a date range."""
    client, prop = _ga4_client_and_property()
    if client is None or prop is None:
        return []

    from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest

    request = RunReportRequest(
        property=prop,
        date_ranges=[DateRange(start_date=start.isoformat(), end_date=end.isoformat())],
        dimensions=[Dimension(name="date")],
        metrics=[
            Metric(name="activeUsers"),
            Metric(name="sessions"),
            Metric(name="screenPageViews"),
        ],
        limit=400,
    )
    try:
        resp = client.run_report(request)
    except Exception:  # noqa: BLE001
        return []

    rows: list[dict[str, Any]] = []
    for r in resp.rows:
        raw_date = r.dimension_values[0].value or ""
        day = date(
            int(raw_date[0:4]), int(raw_date[4:6]), int(raw_date[6:8])
        ) if len(raw_date) == 8 else start
        rows.append(
            {
                "date": day.isoformat(),
                "weekday": ["월", "화", "수", "목", "금", "토", "일"][day.weekday()],
                "active_users": int(float(r.metric_values[0].value or 0)),
                "sessions": int(float(r.metric_values[1].value or 0)),
                "page_views": int(float(r.metric_values[2].value or 0)),
            }
        )
    return sorted(rows, key=lambda x: x["date"])