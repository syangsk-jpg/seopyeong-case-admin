"""SQLite schema, connection helpers, and CRUD for ad/traffic metrics."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS ad_metrics_hourly (
    ts_hour TEXT NOT NULL,
    source TEXT NOT NULL,
    cost REAL NOT NULL DEFAULT 0,
    clicks INTEGER NOT NULL DEFAULT 0,
    impressions INTEGER NOT NULL DEFAULT 0,
    cpc REAL,
    conversion_value REAL NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY (ts_hour, source)
);

CREATE TABLE IF NOT EXISTS traffic_metrics_hourly (
    ts_hour TEXT NOT NULL PRIMARY KEY,
    active_users REAL NOT NULL DEFAULT 0,
    sessions INTEGER NOT NULL DEFAULT 0,
    bounce_rate REAL,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    alert_type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload_json TEXT
);

CREATE TABLE IF NOT EXISTS oauth_tokens (
    provider TEXT NOT NULL PRIMARY KEY,
    access_token TEXT,
    refresh_token TEXT,
    expires_at TEXT,
    extra_json TEXT,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_ad_ts ON ad_metrics_hourly(ts_hour);
CREATE INDEX IF NOT EXISTS idx_traffic_ts ON traffic_metrics_hourly(ts_hour);
CREATE INDEX IF NOT EXISTS idx_alert_created ON alert_log(created_at DESC);
"""


def ensure_db_path(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    ensure_db_path(config.DATABASE_PATH)
    conn = sqlite3.connect(str(config.DATABASE_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _migrate_ad_conversion_value(conn)
        _migrate_naver_weekly_tables(conn)
        _migrate_naver_keyword_top_clicks(conn)
        _migrate_naver_week_snapshot_clicks(conn)
        _migrate_naver_week_campaign_clicks(conn)
        _migrate_naver_week_snapshot_cost(conn)
        _migrate_naver_week_campaign_cost(conn)
        _migrate_google_weekly_tables(conn)
        _migrate_google_ai_proposals(conn)
        _migrate_ga4_weekly_tables(conn)


def _migrate_naver_week_snapshot_clicks(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='naver_week_snapshot'")
    if cur.fetchone() is None:
        return
    cur = conn.execute("PRAGMA table_info(naver_week_snapshot)")
    cols = {row[1] for row in cur.fetchall()}
    if "total_clicks" not in cols:
        conn.execute(
            "ALTER TABLE naver_week_snapshot ADD COLUMN total_clicks INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_naver_week_snapshot_cost(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='naver_week_snapshot'")
    if cur.fetchone() is None:
        return
    cur = conn.execute("PRAGMA table_info(naver_week_snapshot)")
    cols = {row[1] for row in cur.fetchall()}
    if "total_cost" not in cols:
        conn.execute(
            "ALTER TABLE naver_week_snapshot ADD COLUMN total_cost REAL NOT NULL DEFAULT 0"
        )


def _migrate_naver_week_campaign_cost(conn: sqlite3.Connection) -> None:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='naver_week_campaign_views'"
    )
    if cur.fetchone() is None:
        return
    cur = conn.execute("PRAGMA table_info(naver_week_campaign_views)")
    cols = {row[1] for row in cur.fetchall()}
    if "cost" not in cols:
        conn.execute(
            "ALTER TABLE naver_week_campaign_views ADD COLUMN cost REAL NOT NULL DEFAULT 0"
        )


def _migrate_naver_week_campaign_clicks(conn: sqlite3.Connection) -> None:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='naver_week_campaign_views'"
    )
    if cur.fetchone() is None:
        return
    cur = conn.execute("PRAGMA table_info(naver_week_campaign_views)")
    cols = {row[1] for row in cur.fetchall()}
    if "clicks" not in cols:
        conn.execute(
            "ALTER TABLE naver_week_campaign_views ADD COLUMN clicks INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_naver_keyword_top_clicks(conn: sqlite3.Connection) -> None:
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='naver_week_keyword_top'")
    if cur.fetchone() is None:
        return
    cur = conn.execute("PRAGMA table_info(naver_week_keyword_top)")
    cols = {row[1] for row in cur.fetchall()}
    if "clicks" not in cols:
        conn.execute(
            "ALTER TABLE naver_week_keyword_top ADD COLUMN clicks INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_naver_weekly_tables(conn: sqlite3.Connection) -> None:
    """Ensure weekly Naver impression tables exist (older DB files)."""
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS naver_week_snapshot (
            week_start TEXT NOT NULL PRIMARY KEY,
            week_end TEXT NOT NULL,
            total_impressions INTEGER NOT NULL DEFAULT 0,
            synced_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS naver_week_campaign_views (
            week_start TEXT NOT NULL,
            ncc_campaign_id TEXT NOT NULL,
            campaign_name TEXT,
            impressions INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (week_start, ncc_campaign_id)
        );
        CREATE TABLE IF NOT EXISTS naver_week_keyword_top (
            week_start TEXT NOT NULL,
            rank INTEGER NOT NULL,
            ncc_keyword_id TEXT NOT NULL,
            keyword TEXT NOT NULL,
            campaign_name TEXT,
            impressions INTEGER NOT NULL DEFAULT 0,
            clicks INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (week_start, rank)
        );
        CREATE INDEX IF NOT EXISTS idx_naver_week_kw ON naver_week_keyword_top(week_start);
        """
    )


def _migrate_ad_conversion_value(conn: sqlite3.Connection) -> None:
    cur = conn.execute("PRAGMA table_info(ad_metrics_hourly)")
    cols = {row[1] for row in cur.fetchall()}
    if "conversion_value" not in cols:
        conn.execute(
            "ALTER TABLE ad_metrics_hourly ADD COLUMN conversion_value REAL NOT NULL DEFAULT 0"
        )


def upsert_ad_hourly(
    rows: list[dict[str, Any]],
) -> None:
    if not rows:
        return
    now = datetime.utcnow().isoformat() + "Z"
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO ad_metrics_hourly (ts_hour, source, cost, clicks, impressions, cpc, conversion_value, fetched_at)
            VALUES (:ts_hour, :source, :cost, :clicks, :impressions, :cpc, :conversion_value, :fetched_at)
            ON CONFLICT(ts_hour, source) DO UPDATE SET
                cost = excluded.cost,
                clicks = excluded.clicks,
                impressions = excluded.impressions,
                cpc = excluded.cpc,
                conversion_value = excluded.conversion_value,
                fetched_at = excluded.fetched_at
            """,
            [
                {
                    **r,
                    "conversion_value": float(r.get("conversion_value") or 0),
                    "fetched_at": r.get("fetched_at") or now,
                }
                for r in rows
            ],
        )


def upsert_traffic_hourly(rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    now = datetime.utcnow().isoformat() + "Z"
    with get_connection() as conn:
        conn.executemany(
            """
            INSERT INTO traffic_metrics_hourly (ts_hour, active_users, sessions, bounce_rate, fetched_at)
            VALUES (:ts_hour, :active_users, :sessions, :bounce_rate, :fetched_at)
            ON CONFLICT(ts_hour) DO UPDATE SET
                active_users = excluded.active_users,
                sessions = excluded.sessions,
                bounce_rate = excluded.bounce_rate,
                fetched_at = excluded.fetched_at
            """,
            [{**r, "fetched_at": r.get("fetched_at") or now} for r in rows],
        )


def insert_alert(alert_type: str, message: str, payload_json: str | None = None) -> None:
    created = datetime.utcnow().isoformat() + "Z"
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO alert_log (created_at, alert_type, message, payload_json)
            VALUES (?, ?, ?, ?)
            """,
            (created, alert_type, message, payload_json),
        )


def fetch_ad_range(ts_hour_start: str, ts_hour_end: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT ts_hour, source, cost, clicks, impressions, cpc, conversion_value, fetched_at
            FROM ad_metrics_hourly
            WHERE ts_hour >= ? AND ts_hour <= ?
            ORDER BY ts_hour, source
            """,
            (ts_hour_start, ts_hour_end),
        )
        return cur.fetchall()


def fetch_traffic_range(ts_hour_start: str, ts_hour_end: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT ts_hour, active_users, sessions, bounce_rate, fetched_at
            FROM traffic_metrics_hourly
            WHERE ts_hour >= ? AND ts_hour <= ?
            ORDER BY ts_hour
            """,
            (ts_hour_start, ts_hour_end),
        )
        return cur.fetchall()


def fetch_recent_alerts(limit: int = 100) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT id, created_at, alert_type, message, payload_json
            FROM alert_log
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return cur.fetchall()


def get_token_row(provider: str) -> sqlite3.Row | None:
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT * FROM oauth_tokens WHERE provider = ?",
            (provider,),
        )
        return cur.fetchone()


def replace_naver_week_data(
    week_start: str,
    week_end: str,
    total_impressions: int,
    total_clicks: int,
    total_cost: float,
    campaigns: list[dict[str, Any]],
    top_keywords: list[dict[str, Any]],
) -> None:
    """Replace one week's Naver 주간 스냅샷(캠페인·키워드)."""
    now = datetime.utcnow().isoformat() + "Z"
    with get_connection() as conn:
        conn.execute("DELETE FROM naver_week_campaign_views WHERE week_start = ?", (week_start,))
        conn.execute("DELETE FROM naver_week_keyword_top WHERE week_start = ?", (week_start,))
        conn.execute(
            """
            INSERT INTO naver_week_snapshot (week_start, week_end, total_impressions, total_clicks, total_cost, synced_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(week_start) DO UPDATE SET
                week_end = excluded.week_end,
                total_impressions = excluded.total_impressions,
                total_clicks = excluded.total_clicks,
                total_cost = excluded.total_cost,
                synced_at = excluded.synced_at
            """,
            (week_start, week_end, int(total_impressions), int(total_clicks), float(total_cost), now),
        )
        conn.executemany(
            """
            INSERT INTO naver_week_campaign_views (week_start, ncc_campaign_id, campaign_name, impressions, clicks, cost)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    week_start,
                    row["ncc_campaign_id"],
                    row.get("campaign_name"),
                    int(row["impressions"]),
                    int(row.get("clicks") or 0),
                    float(row.get("cost") or 0),
                )
                for row in campaigns
            ],
        )
        conn.executemany(
            """
            INSERT INTO naver_week_keyword_top
                (week_start, rank, ncc_keyword_id, keyword, campaign_name, impressions, clicks)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    week_start,
                    int(row["rank"]),
                    row["ncc_keyword_id"],
                    row["keyword"],
                    row.get("campaign_name"),
                    int(row["impressions"]),
                    int(row.get("clicks") or 0),
                )
                for row in top_keywords
            ],
        )


def fetch_naver_week_snapshots() -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT week_start, week_end, total_impressions, total_clicks, total_cost, synced_at
            FROM naver_week_snapshot
            ORDER BY week_start DESC
            """
        )
        return cur.fetchall()


def fetch_naver_week_campaign_views(week_start: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT week_start, ncc_campaign_id, campaign_name, impressions, clicks, cost
            FROM naver_week_campaign_views
            WHERE week_start = ?
            ORDER BY cost DESC, clicks DESC, impressions DESC
            """,
            (week_start,),
        )
        return cur.fetchall()


def fetch_naver_week_keyword_top(week_start: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT week_start, rank, ncc_keyword_id, keyword, campaign_name, impressions, clicks
            FROM naver_week_keyword_top
            WHERE week_start = ?
            ORDER BY rank ASC
            """,
            (week_start,),
        )
        return cur.fetchall()


def upsert_oauth_token(
    provider: str,
    access_token: str | None,
    refresh_token: str | None,
    expires_at: str | None,
    extra_json: str | None = None,
) -> None:
    now = datetime.utcnow().isoformat() + "Z"
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO oauth_tokens (provider, access_token, refresh_token, expires_at, extra_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                expires_at = excluded.expires_at,
                extra_json = excluded.extra_json,
                updated_at = excluded.updated_at
            """,
            (provider, access_token, refresh_token, expires_at, extra_json, now),
        )


def latest_ad_ts_hour() -> str | None:
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(ts_hour) AS m FROM ad_metrics_hourly").fetchone()
        return str(row["m"]) if row and row["m"] is not None else None


def latest_traffic_ts_hour() -> str | None:
    with get_connection() as conn:
        row = conn.execute("SELECT MAX(ts_hour) AS m FROM traffic_metrics_hourly").fetchone()
        return str(row["m"]) if row and row["m"] is not None else None


def _migrate_google_weekly_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS google_week_snapshot (
            week_start TEXT NOT NULL PRIMARY KEY,
            week_end TEXT NOT NULL,
            total_impressions INTEGER NOT NULL DEFAULT 0,
            total_clicks INTEGER NOT NULL DEFAULT 0,
            total_cost REAL NOT NULL DEFAULT 0,
            total_conversions REAL NOT NULL DEFAULT 0,
            synced_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS google_week_campaign_views (
            week_start TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            campaign_name TEXT,
            campaign_type TEXT,
            impressions INTEGER NOT NULL DEFAULT 0,
            clicks INTEGER NOT NULL DEFAULT 0,
            cost REAL NOT NULL DEFAULT 0,
            conversions REAL NOT NULL DEFAULT 0,
            budget_resource_name TEXT,
            PRIMARY KEY (week_start, campaign_id)
        );
        CREATE TABLE IF NOT EXISTS google_week_keyword_top (
            week_start TEXT NOT NULL,
            rank INTEGER NOT NULL,
            criterion_id TEXT NOT NULL,
            keyword TEXT NOT NULL,
            campaign_name TEXT,
            impressions INTEGER NOT NULL DEFAULT 0,
            clicks INTEGER NOT NULL DEFAULT 0,
            cost REAL NOT NULL DEFAULT 0,
            ad_group_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (week_start, rank)
        );
        CREATE INDEX IF NOT EXISTS idx_google_week_kw ON google_week_keyword_top(week_start);
        """
    )


def _migrate_google_ai_proposals(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS google_ai_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            period_type TEXT NOT NULL,
            period_start TEXT NOT NULL,
            period_label TEXT,
            action_type TEXT NOT NULL,
            target_type TEXT NOT NULL,
            campaign_name TEXT,
            keyword_text TEXT,
            match_type TEXT,
            current_value TEXT,
            proposed_value TEXT,
            change_pct REAL,
            rationale TEXT,
            priority TEXT,
            confidence TEXT,
            resolved_campaign_id TEXT,
            resolved_ad_group_id TEXT,
            resolved_criterion_id TEXT,
            resolved_budget_resource_name TEXT,
            resolution_status TEXT NOT NULL DEFAULT 'unresolved',
            resolution_note TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            reviewed_at TEXT,
            applied_at TEXT,
            apply_result_json TEXT,
            raw_gemini_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_google_ai_status ON google_ai_proposals(status);
        """
    )


def _migrate_ga4_weekly_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ga4_week_snapshot (
            week_start TEXT NOT NULL PRIMARY KEY,
            week_end TEXT NOT NULL,
            total_sessions INTEGER NOT NULL DEFAULT 0,
            total_active_users INTEGER NOT NULL DEFAULT 0,
            synced_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ga4_week_channel (
            week_start TEXT NOT NULL,
            channel TEXT NOT NULL,
            sessions INTEGER NOT NULL DEFAULT 0,
            active_users INTEGER NOT NULL DEFAULT 0,
            engaged_sessions INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (week_start, channel)
        );
        CREATE TABLE IF NOT EXISTS ga4_week_event (
            week_start TEXT NOT NULL,
            event_name TEXT NOT NULL,
            event_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (week_start, event_name)
        );
        CREATE TABLE IF NOT EXISTS ga4_week_page (
            week_start TEXT NOT NULL,
            page_path TEXT NOT NULL,
            views INTEGER NOT NULL DEFAULT 0,
            active_users INTEGER NOT NULL DEFAULT 0,
            avg_engagement_sec REAL NOT NULL DEFAULT 0,
            PRIMARY KEY (week_start, page_path)
        );
        """
    )


def replace_google_week_data(
    week_start: str,
    week_end: str,
    total_impressions: int,
    total_clicks: int,
    total_cost: float,
    total_conversions: float,
    campaigns: list[dict[str, Any]],
    top_keywords: list[dict[str, Any]],
) -> None:
    """Replace one week's Google Ads 주간 스냅샷(캠페인·키워드)."""
    now = datetime.utcnow().isoformat() + "Z"
    with get_connection() as conn:
        conn.execute("DELETE FROM google_week_campaign_views WHERE week_start = ?", (week_start,))
        conn.execute("DELETE FROM google_week_keyword_top WHERE week_start = ?", (week_start,))
        conn.execute(
            """
            INSERT INTO google_week_snapshot
                (week_start, week_end, total_impressions, total_clicks, total_cost, total_conversions, synced_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(week_start) DO UPDATE SET
                week_end = excluded.week_end,
                total_impressions = excluded.total_impressions,
                total_clicks = excluded.total_clicks,
                total_cost = excluded.total_cost,
                total_conversions = excluded.total_conversions,
                synced_at = excluded.synced_at
            """,
            (
                week_start,
                week_end,
                int(total_impressions),
                int(total_clicks),
                float(total_cost),
                float(total_conversions),
                now,
            ),
        )
        conn.executemany(
            """
            INSERT INTO google_week_campaign_views
                (week_start, campaign_id, campaign_name, campaign_type, impressions, clicks, cost,
                 conversions, budget_resource_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    week_start,
                    str(row["campaign_id"]),
                    row.get("campaign_name"),
                    row.get("campaign_type"),
                    int(row.get("impressions") or 0),
                    int(row.get("clicks") or 0),
                    float(row.get("cost") or 0),
                    float(row.get("conversions") or 0),
                    row.get("budget_resource_name"),
                )
                for row in campaigns
            ],
        )
        conn.executemany(
            """
            INSERT INTO google_week_keyword_top
                (week_start, rank, criterion_id, keyword, campaign_name, impressions, clicks, cost, ad_group_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    week_start,
                    int(row["rank"]),
                    str(row["criterion_id"]),
                    row["keyword"],
                    row.get("campaign_name"),
                    int(row.get("impressions") or 0),
                    int(row.get("clicks") or 0),
                    float(row.get("cost") or 0),
                    str(row.get("ad_group_id") or ""),
                )
                for row in top_keywords
            ],
        )


def fetch_google_week_snapshots() -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT week_start, week_end, total_impressions, total_clicks, total_cost,
                   total_conversions, synced_at
            FROM google_week_snapshot
            ORDER BY week_start DESC
            """
        )
        return cur.fetchall()


def fetch_latest_google_week_start() -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT week_start FROM google_week_snapshot ORDER BY week_start DESC LIMIT 1"
        ).fetchone()
        return str(row["week_start"]) if row else None


def fetch_google_week_campaign_views(week_start: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT week_start, campaign_id, campaign_name, campaign_type, impressions, clicks,
                   cost, conversions, budget_resource_name
            FROM google_week_campaign_views
            WHERE week_start = ?
            ORDER BY cost DESC, clicks DESC, impressions DESC
            """,
            (week_start,),
        )
        return cur.fetchall()


def fetch_google_week_keyword_top(week_start: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT week_start, rank, criterion_id, keyword, campaign_name, impressions, clicks,
                   cost, ad_group_id
            FROM google_week_keyword_top
            WHERE week_start = ?
            ORDER BY rank ASC
            """,
            (week_start,),
        )
        return cur.fetchall()


def insert_google_ai_proposals(rows: list[dict[str, Any]]) -> list[int]:
    if not rows:
        return []
    now = datetime.utcnow().isoformat() + "Z"
    ids: list[int] = []
    with get_connection() as conn:
        for row in rows:
            cur = conn.execute(
                """
                INSERT INTO google_ai_proposals (
                    created_at, period_type, period_start, period_label, action_type, target_type,
                    campaign_name, keyword_text, match_type, current_value, proposed_value, change_pct,
                    rationale, priority, confidence, resolved_campaign_id, resolved_ad_group_id,
                    resolved_criterion_id, resolved_budget_resource_name, resolution_status,
                    resolution_note, status, raw_gemini_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    row.get("period_type"),
                    row.get("period_start"),
                    row.get("period_label"),
                    row.get("action_type"),
                    row.get("target_type"),
                    row.get("campaign_name"),
                    row.get("keyword_text"),
                    row.get("match_type"),
                    row.get("current_value"),
                    row.get("proposed_value"),
                    row.get("change_pct"),
                    row.get("rationale"),
                    row.get("priority"),
                    row.get("confidence"),
                    row.get("resolved_campaign_id"),
                    row.get("resolved_ad_group_id"),
                    row.get("resolved_criterion_id"),
                    row.get("resolved_budget_resource_name"),
                    row.get("resolution_status") or "unresolved",
                    row.get("resolution_note"),
                    row.get("status") or "pending",
                    row.get("raw_gemini_json"),
                ),
            )
            ids.append(int(cur.lastrowid))
    return ids


def fetch_google_ai_proposals(status: str | None = None) -> list[sqlite3.Row]:
    with get_connection() as conn:
        if status:
            cur = conn.execute(
                "SELECT * FROM google_ai_proposals WHERE status = ? ORDER BY id DESC",
                (status,),
            )
        else:
            cur = conn.execute("SELECT * FROM google_ai_proposals ORDER BY id DESC")
        return cur.fetchall()


def fetch_google_ai_proposal(proposal_id: int) -> sqlite3.Row | None:
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM google_ai_proposals WHERE id = ?", (int(proposal_id),))
        return cur.fetchone()


def update_google_ai_proposal(proposal_id: int, **fields: Any) -> None:
    if not fields:
        return
    allowed = {
        "status",
        "reviewed_at",
        "applied_at",
        "apply_result_json",
        "resolution_status",
        "resolution_note",
        "resolved_campaign_id",
        "resolved_ad_group_id",
        "resolved_criterion_id",
        "resolved_budget_resource_name",
    }
    cols = [(k, v) for k, v in fields.items() if k in allowed]
    if not cols:
        return
    sets = ", ".join(f"{k} = ?" for k, _ in cols)
    vals = [v for _, v in cols] + [int(proposal_id)]
    with get_connection() as conn:
        conn.execute(f"UPDATE google_ai_proposals SET {sets} WHERE id = ?", vals)


def replace_ga4_week_data(
    week_start: str,
    week_end: str,
    channels: list[dict[str, Any]],
    events: list[dict[str, Any]],
    pages: list[dict[str, Any]] | None = None,
) -> None:
    """Replace one week's GA4 주간 스냅샷(채널·이벤트·페이지)."""
    now = datetime.utcnow().isoformat() + "Z"
    pages = pages or []
    total_sessions = sum(int(r.get("sessions") or 0) for r in channels)
    total_users = sum(int(r.get("active_users") or 0) for r in channels)
    with get_connection() as conn:
        conn.execute("DELETE FROM ga4_week_channel WHERE week_start = ?", (week_start,))
        conn.execute("DELETE FROM ga4_week_event WHERE week_start = ?", (week_start,))
        conn.execute("DELETE FROM ga4_week_page WHERE week_start = ?", (week_start,))
        conn.execute(
            """
            INSERT INTO ga4_week_snapshot
                (week_start, week_end, total_sessions, total_active_users, synced_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(week_start) DO UPDATE SET
                week_end = excluded.week_end,
                total_sessions = excluded.total_sessions,
                total_active_users = excluded.total_active_users,
                synced_at = excluded.synced_at
            """,
            (week_start, week_end, int(total_sessions), int(total_users), now),
        )
        conn.executemany(
            """
            INSERT INTO ga4_week_channel
                (week_start, channel, sessions, active_users, engaged_sessions)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    week_start,
                    str(row.get("channel") or "Unassigned"),
                    int(row.get("sessions") or 0),
                    int(row.get("active_users") or 0),
                    int(row.get("engaged_sessions") or 0),
                )
                for row in channels
            ],
        )
        conn.executemany(
            """
            INSERT INTO ga4_week_event (week_start, event_name, event_count)
            VALUES (?, ?, ?)
            """,
            [
                (
                    week_start,
                    str(row.get("event_name") or ""),
                    int(row.get("event_count") or 0),
                )
                for row in events
                if str(row.get("event_name") or "")
            ],
        )
        conn.executemany(
            """
            INSERT INTO ga4_week_page
                (week_start, page_path, views, active_users, avg_engagement_sec)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                (
                    week_start,
                    str(row.get("page_path") or ""),
                    int(row.get("views") or 0),
                    int(row.get("active_users") or 0),
                    float(row.get("avg_engagement_sec") or 0),
                )
                for row in pages
                if str(row.get("page_path") or "")
            ],
        )


def fetch_ga4_week_snapshots() -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT week_start, week_end, total_sessions, total_active_users, synced_at
            FROM ga4_week_snapshot
            ORDER BY week_start DESC
            """
        )
        return cur.fetchall()


def fetch_ga4_week_channel(week_start: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT week_start, channel, sessions, active_users, engaged_sessions
            FROM ga4_week_channel
            WHERE week_start = ?
            ORDER BY sessions DESC
            """,
            (week_start,),
        )
        return cur.fetchall()


def fetch_ga4_week_event(week_start: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT week_start, event_name, event_count
            FROM ga4_week_event
            WHERE week_start = ?
            ORDER BY event_count DESC
            """,
            (week_start,),
        )
        return cur.fetchall()


def fetch_ga4_week_page(week_start: str) -> list[sqlite3.Row]:
    with get_connection() as conn:
        cur = conn.execute(
            """
            SELECT week_start, page_path, views, active_users, avg_engagement_sec
            FROM ga4_week_page
            WHERE week_start = ?
            ORDER BY views DESC
            """,
            (week_start,),
        )
        return cur.fetchall()
