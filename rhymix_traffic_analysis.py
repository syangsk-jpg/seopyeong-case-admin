"""Read-only Rhymix traffic diagnostics for the AI dashboard.

Only aggregate statistics are returned. Raw IP addresses never leave the server
and are not included in the AI context.
"""
from __future__ import annotations

import shlex
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Any

import config


def _credentials() -> dict[str, str]:
    values: dict[str, str] = {}
    path_value = str(getattr(config, "RHYMIX_CREDENTIALS_FILE", "") or "").strip()
    if path_value:
        path = Path(path_value).expanduser()
        if path.is_file():
            for raw in path.read_text(encoding="utf-8-sig").splitlines():
                line = raw.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()

    overrides = {
        "SFTP_HOST": getattr(config, "RHYMIX_SSH_HOST", None),
        "SFTP_PORT": getattr(config, "RHYMIX_SSH_PORT", None),
        "SFTP_USER": getattr(config, "RHYMIX_SSH_USER", None),
        "SFTP_PASSWORD": getattr(config, "RHYMIX_SSH_PASSWORD", None),
        "DB_USER": getattr(config, "RHYMIX_DB_USER", None),
        "DB_PASSWORD": getattr(config, "RHYMIX_DB_PASSWORD", None),
        "DB_NAME": getattr(config, "RHYMIX_DB_NAME", None),
        "DB_PREFIX": getattr(config, "RHYMIX_DB_PREFIX", None),
    }
    for key, value in overrides.items():
        if value not in (None, ""):
            values[key] = str(value)
    return values


def _validate_prefix(value: str) -> str:
    prefix = value or "wp_"
    if not prefix.replace("_", "").isalnum():
        raise ValueError("DB prefix contains unsupported characters")
    return prefix


def _query_rows(sql: str) -> list[list[str]]:
    creds = _credentials()
    required = ("SFTP_HOST", "SFTP_USER", "SFTP_PASSWORD", "DB_USER", "DB_PASSWORD", "DB_NAME")
    missing = [key for key in required if not creds.get(key)]
    if missing:
        raise RuntimeError("Rhymix connection settings missing: " + ", ".join(missing))

    try:
        import paramiko
    except ImportError as exc:  # pragma: no cover - dependency is part of requirements
        raise RuntimeError("paramiko is required for Rhymix traffic analysis") from exc

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            creds["SFTP_HOST"],
            int(creds.get("SFTP_PORT", "22")),
            creds["SFTP_USER"],
            creds["SFTP_PASSWORD"],
            timeout=20,
        )
        mysql = " ".join(
            [
                "mysql",
                "-u" + shlex.quote(creds["DB_USER"]),
                "-p" + shlex.quote(creds["DB_PASSWORD"]),
                shlex.quote(creds["DB_NAME"]),
                "--default-character-set=utf8mb4 -N -B",
                "-e",
                shlex.quote(sql),
            ]
        )
        _, stdout, stderr = client.exec_command(mysql, timeout=60)
        output = stdout.read().decode("utf-8", "replace")
        error = stderr.read().decode("utf-8", "replace")
        meaningful = "\n".join(line for line in error.splitlines() if "Warning" not in line).strip()
        if meaningful:
            raise RuntimeError(meaningful[:500])
        return [line.split("\t") for line in output.splitlines() if line.strip()]
    finally:
        client.close()


def _as_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _pct(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 1) if denominator else 0.0


def _period_sql(start: date, end: date, baseline_start: date, prefix: str) -> str:
    start_day, end_day = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")
    baseline_day = baseline_start.strftime("%Y%m%d")
    start_stamp, end_stamp = start_day + "000000", end_day + "235959"
    return f"""
SELECT 'D',regdate,unique_visitor,pageview
FROM {prefix}counter_status
WHERE regdate BETWEEN '{baseline_day}' AND '{end_day}' AND regdate <> '0'
ORDER BY regdate;
SELECT 'L',LEFT(regdate,8),COUNT(*),COUNT(DISTINCT ipaddress),COUNT(DISTINCT user_agent),
SUM(user_agent LIKE '%afma%'),SUM(user_agent LIKE '%trill_%'),
SUM(user_agent='Chrome Privacy Preserving Prefetch Proxy'),
SUM(LOWER(user_agent) LIKE '%bot%' OR LOWER(user_agent) LIKE '%spider%' OR LOWER(user_agent) LIKE '%crawler%'
 OR user_agent LIKE '%SecurityResearch%' OR user_agent LIKE '%crusader-worker%'),
SUM(user_agent LIKE '%Macintosh; Intel Mac OS X 10_15_7%Chrome/142.0.0.0%'),
SUM(user_agent LIKE '%iPhone OS 13_2_3%Version/13.0.3%')
FROM {prefix}counter_log
WHERE regdate BETWEEN '{start_stamp}' AND '{end_stamp}'
GROUP BY LEFT(regdate,8) ORDER BY 2;
SELECT 'H',LEFT(regdate,8),SUBSTRING(regdate,9,2),COUNT(*)
FROM {prefix}counter_log
WHERE regdate BETWEEN '{start_stamp}' AND '{end_stamp}'
GROUP BY LEFT(regdate,8),SUBSTRING(regdate,9,2) ORDER BY 2,3;
"""


def summarize_rows(
    rows: list[list[str]], start: date, end: date, baseline_start: date
) -> dict[str, Any]:
    daily_status: dict[str, dict[str, int]] = {}
    log_stats: dict[str, list[int]] = {}
    hourly: list[dict[str, Any]] = []
    for row in rows:
        if not row:
            continue
        if row[0] == "D" and len(row) >= 4:
            daily_status[row[1]] = {"unique_visitors": _as_int(row[2]), "pageviews": _as_int(row[3])}
        elif row[0] == "L" and len(row) >= 11:
            log_stats[row[1]] = [_as_int(v) for v in row[2:11]]
        elif row[0] == "H" and len(row) >= 4:
            hourly.append({"date": row[1], "hour": _as_int(row[2]), "unique_visitors": _as_int(row[3])})

    period_days: list[dict[str, Any]] = []
    total_uv = total_pv = total_suspicious = 0
    cursor = start
    while cursor <= end:
        key = cursor.strftime("%Y%m%d")
        status = daily_status.get(key, {"unique_visitors": 0, "pageviews": 0})
        stats = log_stats.get(key, [0] * 9)
        # Categories are designed to be mostly disjoint. Cap protects against overlap.
        suspicious = min(stats[0], sum(stats[3:9]))
        uv, pv = status["unique_visitors"], status["pageviews"]
        period_days.append(
            {
                "date": cursor.isoformat(),
                "weekday": ["월", "화", "수", "목", "금", "토", "일"][cursor.weekday()],
                "unique_visitors": uv,
                "pageviews": pv,
                "pageviews_per_visitor": round(pv / uv, 2) if uv else 0.0,
                "distinct_ips": stats[1],
                "distinct_user_agents": stats[2],
                "signals": {
                    "ad_sdk_webview": stats[3],
                    "social_app_webview": stats[4],
                    "prefetch": stats[5],
                    "explicit_bot_or_scanner": stats[6],
                    "repeated_mac_chrome_signature": stats[7],
                    "repeated_old_iphone_signature": stats[8],
                },
                "suspicious_or_low_quality_estimate": suspicious,
                "suspicious_share_pct": _pct(suspicious, stats[0]),
                "human_like_reference_estimate": max(0, uv - suspicious),
            }
        )
        total_uv += uv
        total_pv += pv
        total_suspicious += suspicious
        cursor += timedelta(days=1)

    baseline_values = [
        value["unique_visitors"]
        for key, value in daily_status.items()
        if baseline_start.strftime("%Y%m%d") <= key < start.strftime("%Y%m%d")
    ]
    baseline_median = float(median(baseline_values)) if baseline_values else 0.0
    peaks = sorted(hourly, key=lambda item: item["unique_visitors"], reverse=True)[:8]
    current_daily_avg = total_uv / max(1, len(period_days))
    notes = [
        "Rhymix unique visitors count the first visit from an IP per day; repeat page loads mainly increase pageviews.",
        "Human-like reference is a screening estimate, not a confirmed count of real prospective clients.",
        "No raw IP address is included in this result or sent to the AI model.",
    ]
    return {
        "available": True,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "daily": period_days,
        "totals": {
            "unique_visitors": total_uv,
            "pageviews": total_pv,
            "pageviews_per_visitor": round(total_pv / total_uv, 2) if total_uv else 0.0,
            "suspicious_or_low_quality_estimate": total_suspicious,
            "suspicious_share_pct": _pct(total_suspicious, total_uv),
            "human_like_reference_estimate": max(0, total_uv - total_suspicious),
        },
        "baseline": {
            "days": len(baseline_values),
            "daily_unique_visitor_median": round(baseline_median, 1),
            "current_daily_average": round(current_daily_avg, 1),
            "change_vs_median_pct": round((current_daily_avg / baseline_median - 1) * 100, 1)
            if baseline_median
            else None,
        },
        "top_hourly_spikes": peaks,
        "interpretation_notes": notes,
    }


def build_server_traffic_context(start: date, end: date, baseline_days: int = 28) -> dict[str, Any]:
    if not getattr(config, "RHYMIX_TRAFFIC_ENABLED", False):
        return {"available": False, "reason": "Rhymix server traffic analysis is disabled."}
    if end < start:
        return {"available": False, "reason": "Invalid date range."}
    baseline_start = start - timedelta(days=max(7, int(baseline_days)))
    try:
        prefix = _validate_prefix(str(getattr(config, "RHYMIX_DB_PREFIX", "wp_") or "wp_"))
        rows = _query_rows(_period_sql(start, end, baseline_start, prefix))
        return summarize_rows(rows, start, end, baseline_start)
    except Exception as exc:  # noqa: BLE001 - context must not break the whole dashboard
        return {"available": False, "reason": str(exc)[:500]}
