"""
Google Ads API — hourly metrics per customer.

Requires: pip install google-ads (when not using USE_MOCK_DATA).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import config
import time_utils


def _build_google_ads_client() -> Any | None:
    try:
        from google.ads.googleads.client import GoogleAdsClient
    except ImportError:
        return None
    if not all(
        [
            config.GOOGLE_ADS_DEVELOPER_TOKEN,
            config.GOOGLE_ADS_CLIENT_ID,
            config.GOOGLE_ADS_CLIENT_SECRET,
            config.GOOGLE_ADS_REFRESH_TOKEN,
            config.GOOGLE_ADS_CUSTOMER_ID,
        ]
    ):
        return None
    cfg = {
        "developer_token": config.GOOGLE_ADS_DEVELOPER_TOKEN,
        "client_id": config.GOOGLE_ADS_CLIENT_ID,
        "client_secret": config.GOOGLE_ADS_CLIENT_SECRET,
        "refresh_token": config.GOOGLE_ADS_REFRESH_TOKEN,
        "login_customer_id": (config.GOOGLE_ADS_LOGIN_CUSTOMER_ID or "").replace("-", ""),
        "use_proto_plus": True,
    }
    return GoogleAdsClient.load_from_dict(cfg)


def fetch_google_ads_hourly(start: datetime, end: datetime) -> list[dict[str, Any]]:
    if config.USE_MOCK_DATA:
        from .mock_data import mock_ad_rows

        return [r for r in mock_ad_rows(start, end) if r["source"] == "google"]

    client = _build_google_ads_client()
    if client is None:
        return []

    customer_id = (config.GOOGLE_ADS_CUSTOMER_ID or "").replace("-", "")
    ga = client.get_service("GoogleAdsService")
    start_s = start.date().isoformat()
    end_s = end.date().isoformat()

    query = f"""
        SELECT
          segments.date,
          segments.hour,
          metrics.cost_micros,
          metrics.clicks,
          metrics.impressions,
          metrics.average_cpc,
          metrics.conversions_value
        FROM campaign
        WHERE segments.date BETWEEN '{start_s}' AND '{end_s}'
    """

    rows: list[dict[str, Any]] = []
    try:
        response = ga.search_stream(customer_id=customer_id, query=query)
        z = time_utils.tz()
        for batch in response:
            for row in batch.results:
                d = row.segments.date
                h = int(row.segments.hour)
                dt = datetime.strptime(d, "%Y-%m-%d").replace(hour=h, tzinfo=z)
                cost = float(row.metrics.cost_micros or 0) / 1_000_000
                clicks = int(row.metrics.clicks or 0)
                imp = int(row.metrics.impressions or 0)
                cpc_m = row.metrics.average_cpc
                cpc = float(cpc_m) / 1_000_000 if cpc_m else (cost / clicks if clicks else None)
                cv = float(row.metrics.conversions_value or 0)
                rows.append(
                    {
                        "ts_hour": time_utils.format_ts_hour(dt),
                        "source": "google",
                        "cost": cost,
                        "clicks": clicks,
                        "impressions": imp,
                        "cpc": cpc,
                        "conversion_value": cv,
                    }
                )
    except Exception:  # noqa: BLE001 — GoogleAdsException or network
        return []

    agg: dict[str, dict[str, Any]] = {}
    for r in rows:
        k = r["ts_hour"]
        if k not in agg:
            agg[k] = {
                "ts_hour": k,
                "source": "google",
                "cost": 0.0,
                "clicks": 0,
                "impressions": 0,
                "cpc": None,
                "conversion_value": 0.0,
            }
        agg[k]["cost"] += r["cost"]
        agg[k]["clicks"] += r["clicks"]
        agg[k]["impressions"] += r["impressions"]
        agg[k]["conversion_value"] = float(agg[k]["conversion_value"]) + float(r.get("conversion_value") or 0)
    out = list(agg.values())
    for a in out:
        a["cpc"] = (a["cost"] / a["clicks"]) if a["clicks"] else None
    return sorted(out, key=lambda x: x["ts_hour"])
