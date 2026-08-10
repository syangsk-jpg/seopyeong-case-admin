"""Homepage content metadata helpers for GA4 case-page reporting."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

DEFAULT_HOMEPAGE_ROOT = Path("D:/coding/splawilsan_homepage")


def _homepage_root() -> Path:
    raw = os.environ.get("SPLAW_HOMEPAGE_PATH", "").strip()
    return Path(raw) if raw else DEFAULT_HOMEPAGE_ROOT


def load_case_title_index() -> dict[str, dict[str, Any]]:
    """Load consultation/winning case titles keyed by normalized URL path."""
    path = _homepage_root() / "_data" / "suggestion_case_map.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}

    index: dict[str, dict[str, Any]] = {}
    for entry in (payload.get("by_id") or {}).values():
        for group, case_type in (("consult", "상담사례"), ("winning", "승소사례")):
            for item in entry.get(group) or []:
                raw_url = str(item.get("url") or "")
                url_path = urlsplit(raw_url).path.rstrip("/")
                if not url_path:
                    continue
                index[url_path] = {
                    "case_type": case_type,
                    "title": str(item.get("title") or "").strip(),
                    "field": str(item.get("field") or "").strip(),
                    "url": raw_url,
                }
    return index


def summarize_case_views(
    current_rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Summarize GA4 page rows for consultation and winning case detail pages."""
    title_index = load_case_title_index()

    def collect(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            raw_path = str(row.get("page_path") or "")
            path = urlsplit(raw_path).path.rstrip("/")
            if not (path.startswith("/case_study/") or path.startswith("/winning_cases/")):
                continue
            parts = path.strip("/").split("/")
            if len(parts) != 2 or not parts[1]:
                continue
            meta = title_index.get(path, {})
            case_type = meta.get("case_type") or (
                "상담사례" if path.startswith("/case_study/") else "승소사례"
            )
            title = meta.get("title") or str(row.get("page_title") or path)
            if title.startswith("법무법인 서평 - "):
                title = title.removeprefix("법무법인 서평 - ").strip()
            field = meta.get("field") or ""
            item = result.setdefault(
                path,
                {
                    "path": path,
                    "case_type": case_type,
                    "title": title,
                    "field": field,
                    "views": 0,
                    "active_users": 0,
                },
            )
            item["views"] += int(row.get("views") or 0)
            item["active_users"] += int(row.get("active_users") or 0)
        return result

    current = collect(current_rows)
    previous = collect(previous_rows)
    totals: dict[str, dict[str, int]] = {}
    for case_type in ("상담사례", "승소사례"):
        curr_items = [x for x in current.values() if x["case_type"] == case_type]
        prev_items = [x for x in previous.values() if x["case_type"] == case_type]
        totals[case_type] = {
            "views_curr": sum(int(x["views"]) for x in curr_items),
            "views_prev": sum(int(x["views"]) for x in prev_items),
            "users_curr": sum(int(x["active_users"]) for x in curr_items),
            "users_prev": sum(int(x["active_users"]) for x in prev_items),
            "articles_curr": len(curr_items),
            "articles_prev": len(prev_items),
        }

    top_items: list[dict[str, Any]] = []
    for case_type in ("상담사례", "승소사례"):
        ranked = sorted(
            [x for x in current.values() if x["case_type"] == case_type],
            key=lambda x: x["views"],
            reverse=True,
        )[:3]
        for item in ranked:
            before = previous.get(item["path"], {})
            top_items.append(
                {
                    **item,
                    "views_prev": int(before.get("views") or 0),
                    "view_change": int(item["views"]) - int(before.get("views") or 0),
                }
            )
    return {"totals": totals, "top_items": top_items, "index_size": len(title_index)}