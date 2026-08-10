"""
구글 광고 AI 전략 제안 — 타겟 해석(resolve) + 실제 계정 변경(mutate) 실행.

화이트리스트 7종만 지원: 캠페인 예산 변경/일시중지/재개, 키워드 일시중지/재개/입찰가 변경, 네거티브 키워드 추가.
모든 실행 함수는 config.GOOGLE_ADS_ALLOW_WRITE가 꺼져 있으면 즉시 에러를 반환한다 (앱 UI 승인과는 별개의 2차 안전장치).
예산/입찰가 변경은 저장된 값이 아니라 실행 직전 라이브 조회 값을 기준으로 계산·클램프한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

import config
import database as db
from integrations.google_ads_client import _build_google_ads_client


def _customer_id() -> str:
    return (config.GOOGLE_ADS_CUSTOMER_ID or "").replace("-", "")


def _norm(s: str | None) -> str:
    return (s or "").strip().casefold()


def _require_client() -> Any:
    if not config.GOOGLE_ADS_ALLOW_WRITE:
        raise PermissionError("GOOGLE_ADS_ALLOW_WRITE가 꺼져 있어 실행할 수 없습니다.")
    client = _build_google_ads_client()
    if client is None:
        raise RuntimeError("Google Ads 클라이언트를 생성하지 못했습니다 (자격증명을 확인하세요).")
    return client


def _wrap_error(exc: Exception) -> dict[str, Any]:
    try:
        from google.ads.googleads.errors import GoogleAdsException

        if isinstance(exc, GoogleAdsException):
            msgs = [e.message for e in exc.failure.errors]
            return {"ok": False, "error": "; ".join(msgs) or str(exc)}
    except ImportError:
        pass
    return {"ok": False, "error": str(exc)}


def _clamp_pct(pct: float, max_pct: float) -> float:
    limit = abs(max_pct)
    return max(-limit, min(limit, pct))


# ── 타겟 해석 ──


def resolve_and_store_proposals(
    proposals: list[dict[str, Any]],
    strategy_summary: str,
    period_type: str,
    period_start: str,
    period_label: str,
) -> list[int]:
    """
    Gemini가 준 campaign_name/keyword_text를 최신 동기화 주차의 DB 레코드와 매칭해
    실제 Google Ads 리소스 ID로 해석하고 pending 상태로 저장한다.
    0건 매칭 → unresolved, 2건+ 매칭 → ambiguous, 정확히 1건 → resolved.
    """
    ref_week = db.fetch_latest_google_week_start()
    campaign_rows = [dict(r) for r in db.fetch_google_week_campaign_views(ref_week)] if ref_week else []
    keyword_rows = [dict(r) for r in db.fetch_google_week_keyword_top(ref_week)] if ref_week else []

    campaign_by_name: dict[str, list[dict[str, Any]]] = {}
    for r in campaign_rows:
        campaign_by_name.setdefault(_norm(r.get("campaign_name")), []).append(r)

    keyword_by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for r in keyword_rows:
        key = (_norm(r.get("keyword")), _norm(r.get("campaign_name")))
        keyword_by_key.setdefault(key, []).append(r)

    rows_to_insert: list[dict[str, Any]] = []
    for p in proposals:
        action_type = p.get("action_type")
        target_type = p.get("target_type")
        campaign_name = p.get("campaign_name")
        keyword_text = p.get("keyword_text")

        resolved_campaign_id = None
        resolved_ad_group_id = None
        resolved_criterion_id = None
        resolved_budget_resource_name = None
        resolution_status = "unresolved"
        resolution_note = None

        if not ref_week:
            resolution_note = "동기화된 구글 주간 데이터가 없습니다. 먼저 전체 동기화를 실행하세요."
        elif target_type == "campaign":
            matches = campaign_by_name.get(_norm(campaign_name), [])
            if len(matches) == 1:
                m = matches[0]
                resolved_campaign_id = m.get("campaign_id")
                resolved_budget_resource_name = m.get("budget_resource_name") or None
                resolution_status = "resolved"
            elif len(matches) > 1:
                resolution_status = "ambiguous"
                resolution_note = f"캠페인명이 {len(matches)}건 중복 매칭됨"
            else:
                resolution_note = "캠페인명을 찾을 수 없습니다 (동기화 갱신 필요할 수 있음)."
        elif target_type == "keyword":
            key = (_norm(keyword_text), _norm(campaign_name))
            matches = keyword_by_key.get(key, [])
            if len(matches) == 1:
                m = matches[0]
                resolved_ad_group_id = m.get("ad_group_id") or None
                resolved_criterion_id = m.get("criterion_id")
                resolution_status = "resolved"
            elif len(matches) > 1:
                resolution_status = "ambiguous"
                resolution_note = f"키워드가 {len(matches)}건 중복 매칭됨"
            else:
                resolution_note = "키워드를 찾을 수 없습니다 (동기화 갱신 필요할 수 있음)."
        else:
            resolution_note = f"알 수 없는 target_type: {target_type}"

        rows_to_insert.append(
            {
                "period_type": period_type,
                "period_start": period_start,
                "period_label": period_label,
                "action_type": action_type,
                "target_type": target_type,
                "campaign_name": campaign_name,
                "keyword_text": keyword_text,
                "match_type": p.get("match_type"),
                "current_value": p.get("current_value"),
                "proposed_value": p.get("proposed_value"),
                "change_pct": p.get("change_pct"),
                "rationale": p.get("rationale"),
                "priority": p.get("priority"),
                "confidence": p.get("confidence"),
                "resolved_campaign_id": resolved_campaign_id,
                "resolved_ad_group_id": resolved_ad_group_id,
                "resolved_criterion_id": resolved_criterion_id,
                "resolved_budget_resource_name": resolved_budget_resource_name,
                "resolution_status": resolution_status,
                "resolution_note": resolution_note,
                "raw_gemini_json": p.get("raw_gemini_json") or json.dumps(p, ensure_ascii=False),
            }
        )

    return db.insert_google_ai_proposals(rows_to_insert)


# ── 실행 함수 (화이트리스트 1:1 대응) ──


def _set_campaign_status(campaign_id: str, status_name: str) -> dict[str, Any]:
    try:
        client = _require_client()
    except (PermissionError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}
    try:
        from google.api_core import protobuf_helpers

        customer_id = _customer_id()
        service = client.get_service("CampaignService")
        operation = client.get_type("CampaignOperation")
        campaign = operation.update
        campaign.resource_name = f"customers/{customer_id}/campaigns/{campaign_id}"
        status_enum = client.get_type("CampaignStatusEnum").CampaignStatus
        campaign.status = getattr(status_enum, status_name)
        client.copy_from(operation.update_mask, protobuf_helpers.field_mask(None, campaign._pb))
        response = service.mutate_campaigns(customer_id=customer_id, operations=[operation])
        return {"ok": True, "resource_name": response.results[0].resource_name}
    except Exception as exc:  # noqa: BLE001
        return _wrap_error(exc)


def pause_campaign(campaign_id: str) -> dict[str, Any]:
    return _set_campaign_status(campaign_id, "PAUSED")


def enable_campaign(campaign_id: str) -> dict[str, Any]:
    return _set_campaign_status(campaign_id, "ENABLED")


def _live_budget_amount_micros(client: Any, customer_id: str, budget_resource_name: str) -> int:
    ga = client.get_service("GoogleAdsService")
    query = (
        "SELECT campaign_budget.amount_micros FROM campaign_budget "
        f"WHERE campaign_budget.resource_name = '{budget_resource_name}'"
    )
    for row in ga.search(customer_id=customer_id, query=query):
        return int(row.campaign_budget.amount_micros)
    raise RuntimeError("예산 리소스를 찾을 수 없습니다 (계정에서 변경/삭제됐을 수 있습니다).")


def update_campaign_budget(budget_resource_name: str | None, change_pct: float) -> dict[str, Any]:
    if not budget_resource_name:
        return {"ok": False, "error": "예산 리소스가 해석되지 않았습니다."}
    try:
        client = _require_client()
    except (PermissionError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}
    try:
        from google.api_core import protobuf_helpers

        customer_id = _customer_id()
        current_micros = _live_budget_amount_micros(client, customer_id, budget_resource_name)
        clamped_pct = _clamp_pct(change_pct, config.GOOGLE_ADS_MAX_BUDGET_CHANGE_PCT)
        new_micros = int(round(current_micros * (1 + clamped_pct / 100.0)))
        if new_micros <= 0:
            return {"ok": False, "error": "계산된 신규 예산이 0 이하입니다."}

        service = client.get_service("CampaignBudgetService")
        operation = client.get_type("CampaignBudgetOperation")
        budget = operation.update
        budget.resource_name = budget_resource_name
        budget.amount_micros = new_micros
        client.copy_from(operation.update_mask, protobuf_helpers.field_mask(None, budget._pb))
        response = service.mutate_campaign_budgets(customer_id=customer_id, operations=[operation])
        return {
            "ok": True,
            "resource_name": response.results[0].resource_name,
            "old_amount_won": current_micros // 1_000_000,
            "new_amount_won": new_micros // 1_000_000,
            "requested_change_pct": change_pct,
            "applied_change_pct": clamped_pct,
        }
    except Exception as exc:  # noqa: BLE001
        return _wrap_error(exc)


def _set_keyword_status(ad_group_id: str | None, criterion_id: str | None, status_name: str) -> dict[str, Any]:
    if not ad_group_id or not criterion_id:
        return {"ok": False, "error": "키워드 리소스가 해석되지 않았습니다."}
    try:
        client = _require_client()
    except (PermissionError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}
    try:
        from google.api_core import protobuf_helpers

        customer_id = _customer_id()
        resource_name = f"customers/{customer_id}/adGroupCriteria/{ad_group_id}~{criterion_id}"
        service = client.get_service("AdGroupCriterionService")
        operation = client.get_type("AdGroupCriterionOperation")
        criterion = operation.update
        criterion.resource_name = resource_name
        status_enum = client.get_type("AdGroupCriterionStatusEnum").AdGroupCriterionStatus
        criterion.status = getattr(status_enum, status_name)
        client.copy_from(operation.update_mask, protobuf_helpers.field_mask(None, criterion._pb))
        response = service.mutate_ad_group_criteria(customer_id=customer_id, operations=[operation])
        return {"ok": True, "resource_name": response.results[0].resource_name}
    except Exception as exc:  # noqa: BLE001
        return _wrap_error(exc)


def pause_keyword(ad_group_id: str | None, criterion_id: str | None) -> dict[str, Any]:
    return _set_keyword_status(ad_group_id, criterion_id, "PAUSED")


def enable_keyword(ad_group_id: str | None, criterion_id: str | None) -> dict[str, Any]:
    return _set_keyword_status(ad_group_id, criterion_id, "ENABLED")


def _live_keyword_bid_micros(client: Any, customer_id: str, resource_name: str) -> int:
    ga = client.get_service("GoogleAdsService")
    query = (
        "SELECT ad_group_criterion.cpc_bid_micros FROM ad_group_criterion "
        f"WHERE ad_group_criterion.resource_name = '{resource_name}'"
    )
    for row in ga.search(customer_id=customer_id, query=query):
        return int(row.ad_group_criterion.cpc_bid_micros)
    raise RuntimeError("키워드 리소스를 찾을 수 없습니다 (계정에서 변경/삭제됐을 수 있습니다).")


def update_keyword_bid(ad_group_id: str | None, criterion_id: str | None, change_pct: float) -> dict[str, Any]:
    if not ad_group_id or not criterion_id:
        return {"ok": False, "error": "키워드 리소스가 해석되지 않았습니다."}
    try:
        client = _require_client()
    except (PermissionError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}
    try:
        from google.api_core import protobuf_helpers

        customer_id = _customer_id()
        resource_name = f"customers/{customer_id}/adGroupCriteria/{ad_group_id}~{criterion_id}"
        current_micros = _live_keyword_bid_micros(client, customer_id, resource_name)
        if current_micros <= 0:
            return {
                "ok": False,
                "error": "현재 개별 입찰가가 설정돼 있지 않은 키워드입니다 (자동 입찰 전략 사용 중일 수 있음).",
            }
        clamped_pct = _clamp_pct(change_pct, config.GOOGLE_ADS_MAX_BID_CHANGE_PCT)
        new_micros = int(round(current_micros * (1 + clamped_pct / 100.0)))
        if new_micros <= 0:
            return {"ok": False, "error": "계산된 신규 입찰가가 0 이하입니다."}

        service = client.get_service("AdGroupCriterionService")
        operation = client.get_type("AdGroupCriterionOperation")
        criterion = operation.update
        criterion.resource_name = resource_name
        criterion.cpc_bid_micros = new_micros
        client.copy_from(operation.update_mask, protobuf_helpers.field_mask(None, criterion._pb))
        response = service.mutate_ad_group_criteria(customer_id=customer_id, operations=[operation])
        return {
            "ok": True,
            "resource_name": response.results[0].resource_name,
            "old_bid_won": current_micros // 1_000_000,
            "new_bid_won": new_micros // 1_000_000,
            "requested_change_pct": change_pct,
            "applied_change_pct": clamped_pct,
        }
    except Exception as exc:  # noqa: BLE001
        return _wrap_error(exc)


def add_negative_keyword(campaign_id: str | None, keyword_text: str | None, match_type: str = "BROAD") -> dict[str, Any]:
    if not campaign_id or not keyword_text:
        return {"ok": False, "error": "캠페인 또는 키워드가 해석되지 않았습니다."}
    try:
        client = _require_client()
    except (PermissionError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}
    try:
        customer_id = _customer_id()
        service = client.get_service("CampaignCriterionService")
        operation = client.get_type("CampaignCriterionOperation")
        criterion = operation.create
        criterion.campaign = f"customers/{customer_id}/campaigns/{campaign_id}"
        criterion.negative = True
        mt = (match_type or "BROAD").upper()
        if mt not in ("EXACT", "PHRASE", "BROAD"):
            mt = "BROAD"
        match_enum = client.get_type("KeywordMatchTypeEnum").KeywordMatchType
        criterion.keyword.text = keyword_text
        criterion.keyword.match_type = getattr(match_enum, mt)
        response = service.mutate_campaign_criteria(customer_id=customer_id, operations=[operation])
        return {"ok": True, "resource_name": response.results[0].resource_name}
    except Exception as exc:  # noqa: BLE001
        return _wrap_error(exc)


# ── 디스패처 + 중복 실행 가드 ──


def _find_recent_duplicate(row: dict[str, Any], hours: int = 24) -> str | None:
    """같은 타겟에 같은 액션이 최근 N시간 내 이미 적용됐는지 확인 (진동성 반복 변경 방지)."""
    window_start = (datetime.utcnow() - timedelta(hours=hours)).isoformat() + "Z"
    applied = db.fetch_google_ai_proposals(status="applied")
    for r in applied:
        r = dict(r)
        if int(r["id"]) == int(row["id"]):
            continue
        if r.get("action_type") != row.get("action_type"):
            continue
        same_target = (
            r.get("resolved_campaign_id") == row.get("resolved_campaign_id")
            and r.get("resolved_criterion_id") == row.get("resolved_criterion_id")
        )
        if same_target and str(r.get("applied_at") or "") >= window_start:
            return str(r.get("applied_at"))
    return None


def apply_proposal(proposal_id: int) -> dict[str, Any]:
    """승인된(approved) + 해석된(resolved) 제안을 실제로 실행한다."""
    row = db.fetch_google_ai_proposal(proposal_id)
    if row is None:
        return {"ok": False, "error": "제안을 찾을 수 없습니다."}
    row = dict(row)

    if row.get("status") != "approved":
        return {"ok": False, "error": "승인되지 않은 제안입니다."}
    if row.get("resolution_status") != "resolved":
        return {"ok": False, "error": "타겟이 해석되지 않은 제안입니다."}
    if not config.GOOGLE_ADS_ALLOW_WRITE:
        return {"ok": False, "error": "GOOGLE_ADS_ALLOW_WRITE가 꺼져 있습니다."}

    dup_at = _find_recent_duplicate(row)
    if dup_at is not None:
        return {"ok": False, "error": f"동일한 변경이 {dup_at}에 이미 적용됐습니다 (24시간 내 중복 실행 방지)."}

    action_type = row.get("action_type")
    try:
        if action_type == "campaign_pause":
            result = pause_campaign(row.get("resolved_campaign_id"))
        elif action_type == "campaign_enable":
            result = enable_campaign(row.get("resolved_campaign_id"))
        elif action_type == "keyword_pause":
            result = pause_keyword(row.get("resolved_ad_group_id"), row.get("resolved_criterion_id"))
        elif action_type == "keyword_enable":
            result = enable_keyword(row.get("resolved_ad_group_id"), row.get("resolved_criterion_id"))
        elif action_type == "campaign_budget_change":
            pct = float(row.get("change_pct") or 0)
            result = update_campaign_budget(row.get("resolved_budget_resource_name"), pct)
        elif action_type == "keyword_bid_change":
            pct = float(row.get("change_pct") or 0)
            result = update_keyword_bid(row.get("resolved_ad_group_id"), row.get("resolved_criterion_id"), pct)
        elif action_type == "add_negative_keyword":
            result = add_negative_keyword(
                row.get("resolved_campaign_id"), row.get("keyword_text"), row.get("match_type") or "BROAD"
            )
        else:
            result = {"ok": False, "error": f"알 수 없는 액션 타입: {action_type}"}
    except Exception as exc:  # noqa: BLE001
        result = _wrap_error(exc)

    now = datetime.utcnow().isoformat() + "Z"
    target_label = f"{row.get('campaign_name') or ''} {row.get('keyword_text') or ''}".strip()
    if result.get("ok"):
        db.update_google_ai_proposal(
            proposal_id,
            status="applied",
            applied_at=now,
            apply_result_json=json.dumps(result, ensure_ascii=False),
        )
        db.insert_alert(
            "google_ads_action",
            f"[{action_type}] {target_label} 적용 완료",
            json.dumps(result, ensure_ascii=False),
        )
    else:
        db.update_google_ai_proposal(
            proposal_id,
            status="failed",
            applied_at=now,
            apply_result_json=json.dumps(result, ensure_ascii=False),
        )
        db.insert_alert(
            "google_ads_action",
            f"[{action_type}] {target_label} 실행 실패: {result.get('error')}",
            json.dumps(result, ensure_ascii=False),
        )
    return result
