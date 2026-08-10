"""
Hourly pipeline: sync APIs -> weekly snapshots (new weeks only) -> alerts.

Design: keep collecting into SQLite; do not re-call APIs for weeks/hours already stored.

Run: python scheduler_worker.py

Use a process manager (NSSM, systemd, Windows Task Scheduler) for production.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import alert_engine
import config
import google_weekly_views
import naver_weekly_views
import sync_job

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("scheduler")


def hourly_job() -> None:
    log.info("Starting hourly sync (incremental — past data stays in DB)")
    out = sync_job.run_sync(force=False)
    log.info(
        "Hourly done: mode=%s days=%s ad_points=%s",
        out.get("mode"),
        out.get("days"),
        out.get("ad_points"),
    )

    weeks_n = max(1, int(config.NAVER_WEEKLY_BACK_WEEKS))
    weeks_g = max(1, int(config.GOOGLE_WEEKLY_BACK_WEEKS))
    log.info("Weekly snapshots: collect missing weeks only (skip_existing)")
    try:
        n_rows = naver_weekly_views.sync_naver_weekly_back(weeks_n, skip_existing=True)
        n_new = sum(1 for r in n_rows if r.get("ok") and not r.get("cached"))
        n_cached = sum(1 for r in n_rows if r.get("cached"))
        log.info("Naver weekly: new=%s cached=%s", n_new, n_cached)
    except Exception as exc:  # noqa: BLE001
        log.exception("Naver weekly sync failed: %s", exc)

    try:
        g_rows = google_weekly_views.sync_google_weekly_back(weeks_g, skip_existing=True)
        g_new = sum(1 for r in g_rows if r.get("ok") and not r.get("cached"))
        g_cached = sum(1 for r in g_rows if r.get("cached"))
        log.info("Google weekly: new=%s cached=%s", g_new, g_cached)
    except Exception as exc:  # noqa: BLE001
        log.exception("Google weekly sync failed: %s", exc)

    log.info("Evaluating alerts")
    alert_engine.evaluate_and_alert()
    log.info("Hourly job done")


if __name__ == "__main__":
    hourly_job()
    sched = BlockingScheduler()
    sched.add_job(hourly_job, "interval", hours=1, id="sync_and_alerts", max_instances=1)
    log.info("Scheduler running (1h interval). Ctrl+C to stop.")
    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        pass
