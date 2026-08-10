"""Hour bucketing in configured timezone (align ad + traffic rows)."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import config


def tz() -> ZoneInfo:
    return ZoneInfo(config.TZ)


def floor_to_hour(dt: datetime) -> datetime:
    z = tz()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=z)
    else:
        dt = dt.astimezone(z)
    return dt.replace(minute=0, second=0, microsecond=0)


def format_ts_hour(dt: datetime) -> str:
    """ISO format with offset for stable string keys and merge joins."""
    floored = floor_to_hour(dt)
    return floored.isoformat()


def hour_range_iso(start: datetime, end: datetime) -> tuple[str, str]:
    a = floor_to_hour(start)
    b = floor_to_hour(end)
    return format_ts_hour(a), format_ts_hour(b)


def last_n_days_range(n: int) -> tuple[str, str]:
    z = tz()
    end = datetime.now(z)
    start = end - timedelta(days=n)
    return hour_range_iso(start, end)


def today_range_iso() -> tuple[str, str]:
    z = tz()
    now = datetime.now(z)
    start = datetime.combine(now.date(), datetime.min.time(), tzinfo=z)
    end = now
    return format_ts_hour(start), format_ts_hour(end)


def calendar_week_bounds(week_offset: int) -> tuple[str, str, date, date]:
    """
    Calendar week Mon–Sun in configured TZ (Korea: 월~일).
    week_offset: 0 = this week, -1 = last week, ...
    Returns (ts_hour_start, ts_hour_end, week_monday, week_sunday) for string range filters.
    """
    z = tz()
    today = datetime.now(z).date()
    monday_this = today - timedelta(days=today.weekday())
    monday = monday_this + timedelta(weeks=week_offset)
    sunday = monday + timedelta(days=6)
    start_dt = datetime.combine(monday, datetime.min.time(), tzinfo=z)
    end_dt = datetime.combine(sunday, datetime.min.time(), tzinfo=z).replace(hour=23)
    return format_ts_hour(start_dt), format_ts_hour(end_dt), monday, sunday


def week_label(week_offset: int) -> str:
    _, _, m, s = calendar_week_bounds(week_offset)
    return format_week_range(m, s)


_WEEKDAY_KR = ("월", "화", "수", "목", "금", "토", "일")


def weekday_kr(d: date) -> str:
    return _WEEKDAY_KR[d.weekday()]


def format_date_with_weekday(d: date) -> str:
    """예: 2026-05-25 (일)"""
    return f"{d.isoformat()} ({weekday_kr(d)})"


def format_week_range(mon: date, sun: date) -> str:
    """월~일 구간 + 요일. 예: 2026-05-19 (월) ~ 2026-05-25 (일)"""
    return f"{format_date_with_weekday(mon)} ~ {format_date_with_weekday(sun)}"
