"""Gemini API — 총체적 주간 분석 + 후속 대화."""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Literal

import requests

import config
import database as db
import ga4_weekly_report
import google_weekly_report
import merge
import monthly_report
import naver_weekly_report
import time_utils

_GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# 설정 모델을 먼저 쓰고, 429/503이면 아래 순서로 자동 폴백.
_FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3-flash-preview",
]


def _model_candidates() -> list[str]:
    primary = (config.GEMINI_MODEL or "").strip() or "gemini-2.5-flash"
    ordered = [primary] + [m for m in _FALLBACK_MODELS if m != primary]
    seen: set[str] = set()
    out: list[str] = []
    for m in ordered:
        if m not in seen:
            seen.add(m)
            out.append(m)
    return out


ChatRole = Literal["user", "model"]
ChatMessage = dict[str, Any]  # role, content, display?


def _merged_for_week_off(merged_all, week_off: int):
    w_start, w_end, _, _ = time_utils.calendar_week_bounds(week_off)
    if merged_all.empty:
        return merged_all.iloc[0:0].copy()
    return merged_all[(merged_all["ts_hour"] >= w_start) & (merged_all["ts_hour"] <= w_end)].copy()


def _wap_summary(wap: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "total_clicks",
        "total_cost",
        "total_impressions",
        "blended_ctr_pct",
        "blended_cpc",
        "naver_clicks",
        "google_clicks",
        "naver_cost",
        "google_cost",
        "naver_ctr_pct",
        "google_ctr_pct",
    )
    out: dict[str, Any] = {}
    for k in keys:
        if k in wap:
            v = wap[k]
            out[k] = float(v) if isinstance(v, (int, float)) else v
    return out


def _campaigns_for_context(week_start: str, limit: int = 12) -> list[dict[str, Any]]:
    df = naver_weekly_report.campaigns_df(week_start)
    if df.empty:
        return []
    cols = ["campaign_name", "cost", "clicks", "impressions", "cpc", "cost_share_pct"]
    return df.head(limit)[cols].to_dict(orient="records")


def _keywords_for_context(week_curr: str, week_prev: str, top_n: int = 30) -> list[dict[str, Any]]:
    kw = naver_weekly_report.keyword_clicks_compare(week_curr, week_prev, top_n=top_n)
    if kw.empty:
        return []
    return kw[
        [
            "rank",
            "keyword",
            "campaign_name",
            "clicks_curr",
            "clicks_prev",
            "click_change",
            "click_change_pct",
        ]
    ].to_dict(orient="records")


def _google_campaigns_for_context(week_start: str, limit: int = 12) -> list[dict[str, Any]]:
    df = google_weekly_report.campaigns_df(week_start)
    if df.empty:
        return []
    cols = ["campaign_name", "campaign_type", "cost", "clicks", "impressions", "cpc", "conversions", "cost_share_pct"]
    return df.head(limit)[cols].to_dict(orient="records")


def _google_keywords_for_context(week_curr: str, week_prev: str, top_n: int = 30) -> list[dict[str, Any]]:
    kw = google_weekly_report.keyword_clicks_compare(week_curr, week_prev, top_n=top_n)
    if kw.empty:
        return []
    return kw[
        [
            "rank",
            "keyword",
            "campaign_name",
            "clicks_curr",
            "clicks_prev",
            "click_change",
            "click_change_pct",
        ]
    ].to_dict(orient="records")


def _recent_weeks_trend(merged_all, weeks: int = 6) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for off in range(-1, -1 - weeks, -1):
        _, _, mon, sun = time_utils.calendar_week_bounds(off)
        ws = mon.isoformat()
        slice_df = _merged_for_week_off(merged_all, off)
        wap = merge.week_ad_performance(slice_df, week_start=ws)
        tot = merge.period_totals(slice_df)
        gw = merge.google_week_totals(slice_df)
        nav = naver_weekly_report.week_totals(ws)
        rows.append(
            {
                "week": time_utils.format_week_range(mon, sun),
                "week_start": ws,
                "integrated": _wap_summary(wap),
                "naver_snapshot": nav,
                "google_hourly": gw,
                "traffic": {
                    "sessions": round(float(tot["total_sessions"]), 1),
                    "active_users": round(float(tot["total_active_users"]), 1),
                    "roas": round(float(tot["roas"]), 3),
                    "conversion_value": round(float(tot["total_conversion_value"]), 0),
                },
            }
        )
    return rows


def _recent_alerts(limit: int = 8) -> list[dict[str, Any]]:
    try:
        db.init_db()
        rows = db.fetch_recent_alerts(limit)
        return [
            {
                "created_at": str(r["created_at"]),
                "type": str(r["alert_type"]),
                "message": str(r["message"]),
            }
            for r in rows
        ]
    except Exception:  # noqa: BLE001
        return []


def _ga4_week_starts_in_range(start: date, end: date) -> list[str]:
    """해당 기간과 겹치는 GA4 주(week_start, 월요일 시작) 목록."""
    try:
        rows = db.fetch_ga4_week_snapshots()
    except Exception:  # noqa: BLE001
        return []
    out: list[str] = []
    for r in rows:
        try:
            ws = date.fromisoformat(str(r["week_start"]))
            we = date.fromisoformat(str(r["week_end"]))
        except Exception:  # noqa: BLE001
            continue
        if we >= start and ws <= end:
            out.append(str(r["week_start"]))
    return sorted(out)


def _ga4_homepage_summary(week_starts: list[str]) -> dict[str, Any]:
    """홈페이지 GA4(유입 채널·주요 전환 이벤트·AI 상담 사용) — 지정 주들을 합산한 요약.

    Gemini에게 "숫자만"이 아니라 채널·이벤트 한글 라벨과 업무 설명까지 함께 건네
    해석·개선점 제안이 가능하도록 합니다.
    """
    from ga4_weekly_views import AI_COUNSELOR_PAGE_PATH, KEY_EVENT_NAMES

    if not week_starts:
        return {"has_data": False}

    sessions = 0
    active_users = 0
    channel_sess: dict[str, int] = {}
    channel_users: dict[str, int] = {}
    event_counts: dict[str, int] = {}
    ai_page_views = 0

    for ws in week_starts:
        tot = ga4_weekly_report.week_totals(ws)
        sessions += int(tot.get("sessions") or 0)
        active_users += int(tot.get("active_users") or 0)
        for r in [dict(x) for x in db.fetch_ga4_week_channel(ws)]:
            ch = str(r["channel"])
            channel_sess[ch] = channel_sess.get(ch, 0) + int(r.get("sessions") or 0)
            channel_users[ch] = channel_users.get(ch, 0) + int(r.get("active_users") or 0)
        for r in [dict(x) for x in db.fetch_ga4_week_event(ws)]:
            en = str(r["event_name"])
            event_counts[en] = event_counts.get(en, 0) + int(r.get("event_count") or 0)
        for r in [dict(x) for x in db.fetch_ga4_week_page(ws)]:
            if str(r["page_path"]) == AI_COUNSELOR_PAGE_PATH:
                ai_page_views += int(r.get("views") or 0)

    if sessions <= 0 and active_users <= 0 and not event_counts:
        return {"has_data": False}

    channels = sorted(
        (
            {
                "channel": c,
                "label": ga4_weekly_report.CHANNEL_LABELS.get(c, c),
                "description": ga4_weekly_report.describe_channel(c),
                "sessions": s,
                "active_users": channel_users.get(c, 0),
            }
            for c, s in channel_sess.items()
        ),
        key=lambda x: -x["sessions"],
    )
    key_events = [
        {
            "event_name": n,
            "label": ga4_weekly_report.EVENT_LABELS.get(n, n),
            "description": ga4_weekly_report.describe_event(n),
            "count": event_counts.get(n, 0),
        }
        for n in KEY_EVENT_NAMES
    ]
    return {
        "has_data": True,
        "sessions": sessions,
        "active_users": active_users,
        "channels": channels,
        "key_conversion_events": key_events,
        "ai_counselor_page_views": ai_page_views,
        "ai_counselor_send_clicks": event_counts.get("ai_counselor_send_click", 0),
    }


def _ga4_homepage_context(curr_start: date, curr_end: date, prev_start: date, prev_end: date) -> dict[str, Any]:
    return {
        "this_period": _ga4_homepage_summary(_ga4_week_starts_in_range(curr_start, curr_end)),
        "prev_period": _ga4_homepage_summary(_ga4_week_starts_in_range(prev_start, prev_end)),
    }


def _build_week_context() -> dict[str, Any]:
    prev_mon, prev_sun, curr_mon, curr_sun, title_span = naver_weekly_report.report_meta_week_pair()
    curr_ws = curr_mon.isoformat()
    prev_ws = prev_mon.isoformat()

    lookback = max(42, int(config.SYNC_LOOKBACK_DAYS))
    merged_all = merge.load_merged_range(*time_utils.last_n_days_range(lookback))

    m_curr = merge.week_ad_performance(
        _merged_for_week_off(merged_all, -1), week_start=curr_ws
    )
    m_prev = merge.week_ad_performance(
        _merged_for_week_off(merged_all, -2), week_start=prev_ws
    )
    t_curr = merge.period_totals(_merged_for_week_off(merged_all, -1))
    t_prev = merge.period_totals(_merged_for_week_off(merged_all, -2))
    g_curr = merge.google_week_totals(_merged_for_week_off(merged_all, -1))
    g_prev = merge.google_week_totals(_merged_for_week_off(merged_all, -2))

    p_tot = naver_weekly_report.week_totals(prev_ws)
    c_tot = naver_weekly_report.week_totals(curr_ws)

    summary_df = naver_weekly_report.build_summary_table(prev_mon, prev_sun, curr_mon, curr_sun)

    return {
        "meta": {
            "timezone": config.TZ,
            "period_type": "week",
            "report_period": title_span,
            "comparison": {
                "this_period": time_utils.format_week_range(curr_mon, curr_sun),
                "prev_period": time_utils.format_week_range(prev_mon, prev_sun),
            },
        },
        "naver_summary_table": summary_df.to_dict(orient="records"),
        "this_period": {
            "range": time_utils.format_week_range(curr_mon, curr_sun),
            "naver_snapshot": c_tot,
            "integrated_ads": _wap_summary(m_curr),
            "google_hourly": g_curr,
            "traffic": t_curr,
            "campaigns": _campaigns_for_context(curr_ws),
            "google_campaigns": _google_campaigns_for_context(curr_ws),
        },
        "prev_period": {
            "range": time_utils.format_week_range(prev_mon, prev_sun),
            "naver_snapshot": p_tot,
            "integrated_ads": _wap_summary(m_prev),
            "google_hourly": g_prev,
            "traffic": t_prev,
            "campaigns": _campaigns_for_context(prev_ws),
            "google_campaigns": _google_campaigns_for_context(prev_ws),
        },
        "keyword_top30_compare": _keywords_for_context(curr_ws, prev_ws, top_n=30),
        "google_keyword_top30_compare": _google_keywords_for_context(curr_ws, prev_ws, top_n=30),
        "campaign_analysis_notes": naver_weekly_report.build_campaign_analysis(
            prev_mon, prev_sun, curr_mon, curr_sun
        ),
        "homepage_ga4": _ga4_homepage_context(curr_mon, curr_sun, prev_mon, prev_sun),
        "recent_weeks_trend": _recent_weeks_trend(merged_all, weeks=6),
        "recent_alerts": _recent_alerts(),
    }


def _build_month_context() -> dict[str, Any]:
    prev_start, prev_end, curr_start, curr_end = monthly_report.report_meta_month_pair()
    curr_ws = curr_start.isoformat()
    prev_ws = prev_start.isoformat()
    title_span = (
        f"{monthly_report.format_month_label(prev_start)} vs "
        f"{monthly_report.format_month_label(curr_start)}"
    )

    z = time_utils.tz()

    def _month_range_iso(d0, d1) -> tuple[str, str]:
        start_dt = datetime.combine(d0, datetime.min.time(), tzinfo=z)
        end_dt = datetime.combine(d1, datetime.min.time(), tzinfo=z).replace(hour=23)
        return time_utils.hour_range_iso(start_dt, end_dt)

    curr_start_iso, curr_end_iso = _month_range_iso(curr_start, curr_end)
    prev_start_iso, prev_end_iso = _month_range_iso(prev_start, prev_end)

    merged_curr = merge.load_merged_range(curr_start_iso, curr_end_iso)
    merged_prev = merge.load_merged_range(prev_start_iso, prev_end_iso)

    m_curr = merge.week_ad_performance(merged_curr, week_start=curr_ws)
    m_prev = merge.week_ad_performance(merged_prev, week_start=prev_ws)
    t_curr = merge.period_totals(merged_curr)
    t_prev = merge.period_totals(merged_prev)
    g_curr = merge.google_week_totals(merged_curr)
    g_prev = merge.google_week_totals(merged_prev)

    n_curr = monthly_report.naver_month_totals(curr_start)
    n_prev = monthly_report.naver_month_totals(prev_start)

    gc_cols = ["campaign_name", "campaign_type", "cost", "clicks", "impressions", "cpc", "conversions", "cost_share_pct"]
    gc_curr_df = monthly_report.google_month_campaigns(curr_start)
    gc_prev_df = monthly_report.google_month_campaigns(prev_start)

    gkw_df = monthly_report.google_month_keywords_compare(curr_start, prev_start, top_n=30)
    gkw_cols = ["rank", "keyword", "campaign_name", "clicks_curr", "clicks_prev", "click_change", "click_change_pct"]

    lookback = max(42, int(config.SYNC_LOOKBACK_DAYS))
    merged_all = merge.load_merged_range(*time_utils.last_n_days_range(lookback))

    return {
        "meta": {
            "timezone": config.TZ,
            "period_type": "month",
            "report_period": title_span,
            "comparison": {
                "this_period": monthly_report.format_month_label(curr_start),
                "prev_period": monthly_report.format_month_label(prev_start),
            },
        },
        "this_period": {
            "range": monthly_report.format_month_label(curr_start),
            "naver_snapshot": n_curr,
            "integrated_ads": _wap_summary(m_curr),
            "google_hourly": g_curr,
            "traffic": t_curr,
            "campaigns": [],
            "google_campaigns": gc_curr_df[gc_cols].to_dict(orient="records") if not gc_curr_df.empty else [],
        },
        "prev_period": {
            "range": monthly_report.format_month_label(prev_start),
            "naver_snapshot": n_prev,
            "integrated_ads": _wap_summary(m_prev),
            "google_hourly": g_prev,
            "traffic": t_prev,
            "campaigns": [],
            "google_campaigns": gc_prev_df[gc_cols].to_dict(orient="records") if not gc_prev_df.empty else [],
        },
        "keyword_top30_compare": [],
        "google_keyword_top30_compare": gkw_df[gkw_cols].to_dict(orient="records") if not gkw_df.empty else [],
        "campaign_analysis_notes": [
            "월간 리포트는 월요일이 속한 주차 스냅샷을 합산한 근사치입니다 (네이버 캠페인별 자동 코멘트는 주간 전용)."
        ],
        "homepage_ga4": _ga4_homepage_context(curr_start, curr_end, prev_start, prev_end),
        "recent_weeks_trend": _recent_weeks_trend(merged_all, weeks=6),
        "recent_alerts": _recent_alerts(),
    }


def build_comprehensive_context(period: Literal["week", "month"] = "week") -> dict[str, Any]:
    """대시보드 전 영역을 아우르는 분석용 컨텍스트. period='week'|'month'."""
    if period == "month":
        return _build_month_context()
    return _build_week_context()


def _system_instruction(context_json: str) -> str:
    return f"""당신은 네이버·구글 검색광고와 웹 트래픽을 함께 보는 시니어 퍼포먼스 마케터입니다.
아래 JSON은 주간 광고 대시보드의 **전체 데이터**입니다. 이 데이터만 근거로 답변하세요.

답변 규칙:
- 항상 **한국어**, 실무 보고서 톤
- 숫자·비율을 구체적으로 인용 (원, 회, %, ROAS)
- 네이버 스냅샷(주간 API 총계)과 통합·구글 수치를 구분해 해석
- `homepage_ga4`(홈페이지 유입 채널·주요 전환 이벤트·AI 상담 사용)는 광고비 관점이 아니라
  "홈페이지에 들어온 다음 실제로 무엇을 했는지"로 해석 — 각 이벤트의 `description`을 참고해
  단순히 숫자를 나열하지 말고 그 숫자가 의미하는 사용자 행동을 풀어서 설명할 것
- 데이터에 없는 내용은 추측하지 말고 "데이터에 없음"이라고 말할 것
- 개발자/기술 용어(api, snapshot, hourly 등)는 사용자에게 노출하지 말 것
- 마크다운 제목(##, ###)과 불릿을 사용

=== 대시보드 전체 데이터 (JSON) ===
{context_json}
"""


def _initial_user_prompt(period: Literal["week", "month"] = "week") -> str:
    unit = "이번 달" if period == "month" else "이번 주"
    prev_unit = "지난 달" if period == "month" else "그 전 주"
    title = "총체적인 월간 광고 분석 보고서" if period == "month" else "총체적인 주간 광고 분석 보고서"
    return f"""위 전체 데이터를 바탕으로 **{title}**를 작성해 주세요.

반드시 아래 섹션을 모두 포함하세요:
1. **한 줄 요약** — {unit} 핵심 메시지
2. **통합 성과** — 클릭·비용·CTR·CPC·ROAS, {prev_unit} 대비
3. **채널별** — 네이버 vs 구글 기여도·효율
4. **캠페인** — 네이버·구글 예산 편중·CPC·클릭 구조
5. **핵심 키워드** — 네이버·구글 각각 TOP30 중 가장 효과적이었던 키워드와 클릭 증감, 왜 효과적이었는지
6. **추세** — 최근 흐름 (개선/악화)
7. **트래픽·전환** — 세션·ROAS 관점
8. **홈페이지에서 일어난 일** — `homepage_ga4` 기준, 어떤 채널로 들어와 어떤 행동(전문분야 클릭, 변호사 소개 체류, 카카오톡/로톡 문의, AI 상담 전송 등)을 했는지 사람이 이해할 수 있게 해석. 방문은 늘었는데 문의·상담 전환이 따라오지 않는 등 "숫자 따로 행동 따로"인 구간이 있으면 짚어줄 것
9. **리스크·이상 징후**
10. **실행 제안** — 우선순위 3~5개 (구체적 액션, 실제로 실행 가능한 수준으로). 홈페이지 개선 제안(어떤 버튼·문구·페이지를 손보면 좋을지)을 최소 1개 이상 포함할 것

분량: 900~1500자. 숫자 근거를 충분히 넣어 주세요."""


def _api_key() -> str | None:
    key = (config.GEMINI_API_KEY or "").strip()
    return key or None


def _to_api_contents(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        role: ChatRole = "model" if m["role"] == "model" else "user"
        out.append({"role": role, "parts": [{"text": str(m["content"])}]})
    return out


def _post_one(model: str, api_key: str, payload: dict[str, Any]) -> tuple[str, int, str]:
    """Returns (text, status_code, error_detail). text non-empty on success."""
    url = f"{_GEMINI_BASE}/{model}:generateContent"
    try:
        r = requests.post(url, params={"key": api_key}, json=payload, timeout=120)
    except requests.RequestException as exc:
        return "", 0, f"네트워크 오류: {exc}"
    if r.ok:
        try:
            body = r.json()
        except ValueError:
            return "", r.status_code, "응답 파싱 실패"
        parts = body.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        text = "\n".join(str(p.get("text", "")) for p in parts if isinstance(p, dict)).strip()
        if not text:
            return "", r.status_code, "빈 응답"
        return text, r.status_code, ""
    detail = ""
    try:
        detail = str(r.json().get("error", {}).get("message", ""))[:200]
    except Exception:  # noqa: BLE001
        detail = (r.text or "")[:200]
    return "", r.status_code, detail


def _call_gemini(
    *,
    system_instruction: str,
    contents: list[dict[str, Any]],
    generation_config: dict[str, Any] | None = None,
) -> tuple[str, str | None]:
    api_key = _api_key()
    if not api_key:
        return "", "Gemini API 키가 설정되지 않았습니다. `.env`에 GEMINI_API_KEY를 넣어 주세요."

    payload: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_instruction}]},
        "contents": contents,
    }
    if generation_config:
        payload["generationConfig"] = generation_config

    last_status = 0
    last_detail = ""
    for model in _model_candidates():
        text, status, detail = _post_one(model, api_key, payload)
        if text:
            return text, None
        last_status, last_detail = status, detail
        # 429(할당량)·503(혼잡)·404(모델없음)이면 다음 후보로
        if status in (429, 503, 404, 400):
            continue
        break

    if last_status == 429:
        return "", (
            "모든 Gemini 모델의 무료 할당량이 소진되었습니다. 잠시 후 다시 시도하거나 "
            "Google AI Studio에서 할당량/결제를 확인하세요."
        )
    msg = f"Gemini API 오류 ({last_status or '연결 실패'})"
    if last_detail:
        msg += f": {last_detail}"
    return "", msg


def start_weekly_chat(period: Literal["week", "month"] = "week") -> tuple[dict[str, Any] | None, str | None]:
    """
    총체적 주간·월간 분석을 시작하고 대화 세션을 반환합니다.
    session: { context_json, messages: [{role, content, display?}, ...] }
    """
    ctx = build_comprehensive_context(period=period)
    context_json = json.dumps(ctx, ensure_ascii=False, indent=2)
    system = _system_instruction(context_json)
    user_prompt = _initial_user_prompt(period=period)

    text, err = _call_gemini(
        system_instruction=system,
        contents=[{"role": "user", "parts": [{"text": user_prompt}]}],
    )
    if err:
        return None, err

    session: dict[str, Any] = {
        "context_json": context_json,
        "report_period": ctx["meta"]["report_period"],
        "messages": [
            {"role": "user", "content": user_prompt, "display": False},
            {"role": "model", "content": text, "display": True},
        ],
    }
    return session, None


def continue_chat(session: dict[str, Any], user_message: str) -> tuple[dict[str, Any] | None, str | None]:
    """후속 질문 — 동일 컨텍스트·대화 이력을 유지합니다."""
    user_message = (user_message or "").strip()
    if not user_message:
        return None, "질문을 입력해 주세요."

    messages: list[ChatMessage] = list(session.get("messages") or [])
    messages.append({"role": "user", "content": user_message, "display": True})

    context_json = str(session.get("context_json") or "")
    if not context_json:
        ctx = build_comprehensive_context()
        context_json = json.dumps(ctx, ensure_ascii=False, indent=2)
        session["context_json"] = context_json

    follow_system = _system_instruction(context_json) + (
        "\n\n사용자가 초기 보고서 이후 추가 질문을 합니다. "
        "앞선 대화 맥락과 위 JSON 데이터를 함께 참고해 답변하세요. "
        "짧은 질문에는 핵심만, 깊은 질문에는 숫자 근거를 들어 설명하세요."
    )

    text, err = _call_gemini(
        system_instruction=follow_system,
        contents=_to_api_contents(messages),
    )
    if err:
        return None, err

    messages.append({"role": "model", "content": text, "display": True})
    session["messages"] = messages
    return session, None


def analyze_weekly() -> tuple[str, str | None]:
    """하위 호환 — 첫 분석 텍스트만 반환."""
    session, err = start_weekly_chat()
    if err or not session:
        return "", err or "분석 실패"
    for m in reversed(session.get("messages") or []):
        if m.get("role") == "model":
            return str(m.get("content") or ""), None
    return "", "분석 결과 없음"


# ── 구글 광고 AI 전략 제안 (구조화 출력) ──

_GOOGLE_ACTION_TYPES = [
    "campaign_budget_change",
    "campaign_pause",
    "campaign_enable",
    "keyword_pause",
    "keyword_enable",
    "keyword_bid_change",
    "add_negative_keyword",
]

_PROPOSAL_RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "strategy_summary": {"type": "STRING"},
        "proposals": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "action_type": {"type": "STRING", "enum": _GOOGLE_ACTION_TYPES},
                    "target_type": {"type": "STRING", "enum": ["campaign", "keyword"]},
                    "campaign_name": {"type": "STRING"},
                    "keyword_text": {"type": "STRING", "nullable": True},
                    "match_type": {
                        "type": "STRING",
                        "enum": ["EXACT", "PHRASE", "BROAD"],
                        "nullable": True,
                    },
                    "current_value": {"type": "STRING", "nullable": True},
                    "proposed_value": {"type": "STRING", "nullable": True},
                    "change_pct": {"type": "NUMBER", "nullable": True},
                    "rationale": {"type": "STRING"},
                    "priority": {"type": "STRING", "enum": ["high", "medium", "low"]},
                    "confidence": {"type": "STRING", "enum": ["high", "medium", "low"]},
                },
                "required": ["action_type", "target_type", "campaign_name", "rationale", "priority", "confidence"],
            },
        },
    },
    "required": ["strategy_summary", "proposals"],
}


def _action_proposal_system_instruction(context_json: str, period: Literal["week", "month"]) -> str:
    unit = "월간" if period == "month" else "주간"
    return f"""당신은 구글 광고(Google Ads) 전략을 담당하는 시니어 퍼포먼스 마케터입니다.
아래 JSON은 {unit} 광고 대시보드의 전체 데이터입니다. 이 데이터만 근거로 판단하세요.

당신의 임무: 데이터를 분석해 **실행 가능한 구글 광고 계정 변경안**을 제안하는 것입니다.
제안은 아래 7가지 액션 타입 중에서만 골라야 하며, 이 외의 어떤 액션도 제안하면 안 됩니다:
- campaign_budget_change: 캠페인 일일 예산 변경
- campaign_pause / campaign_enable: 캠페인 일시중지/재개
- keyword_pause / keyword_enable: 키워드 일시중지/재개
- keyword_bid_change: 키워드 CPC 입찰가 변경
- add_negative_keyword: 캠페인에 네거티브 키워드 추가

규칙:
- campaign_name은 반드시 JSON 데이터에 실제로 존재하는 캠페인명을 정확히 그대로 사용하세요 (지어내지 마세요).
- keyword_text도 반드시 JSON 데이터에 있는 실제 키워드를 사용하세요.
- campaign_budget_change/keyword_bid_change는 change_pct(변동률 %)를 반드시 채우고, 최대 ±{config.GOOGLE_ADS_MAX_BUDGET_CHANGE_PCT:.0f}%를 넘지 마세요 (이 상한을 넘는 제안은 어차피 시스템에서 거부됩니다).
- 광고 중단(pause) 제안은 파급효과가 크므로, 데이터로 명확히 저효율임이 확인된 경우에만 제안하세요.
- rationale은 반드시 한국어로, JSON 데이터의 실제 숫자(클릭·비용·CTR 등)를 인용해서 작성하세요.
- 제안은 최대 {config.GOOGLE_AI_PROPOSAL_MAX_PER_RUN}개까지만 생성하세요. 확신이 낮은 제안은 만들지 마세요.
- strategy_summary는 전체 전략 방향을 2~4문장으로 한국어 요약하세요.
- 데이터가 부족해 제안할 것이 없으면 proposals를 빈 배열로 반환하세요 — 억지로 만들지 마세요.

=== 대시보드 전체 데이터 (JSON) ===
{context_json}
"""


def propose_google_ads_actions(
    period: Literal["week", "month"] = "week",
) -> tuple[list[dict[str, Any]], str, str | None]:
    """
    구글 광고 데이터를 분석해 실행 가능한 전략 제안(구조화 JSON)을 생성합니다.
    Returns: (proposals, strategy_summary, error)
    """
    ctx = build_comprehensive_context(period=period)
    context_json = json.dumps(ctx, ensure_ascii=False, indent=2)
    system = _action_proposal_system_instruction(context_json, period)

    generation_config = {
        "responseMimeType": "application/json",
        "responseSchema": _PROPOSAL_RESPONSE_SCHEMA,
    }

    text, err = _call_gemini(
        system_instruction=system,
        contents=[{"role": "user", "parts": [{"text": "위 데이터를 분석해 전략 제안을 JSON으로 생성해 주세요."}]}],
        generation_config=generation_config,
    )
    if err:
        return [], "", err

    try:
        parsed = json.loads(text)
    except (ValueError, TypeError):
        return [], "", "AI 응답을 JSON으로 해석하지 못했습니다. 다시 시도해 주세요."

    proposals = parsed.get("proposals")
    summary = str(parsed.get("strategy_summary") or "")
    if not isinstance(proposals, list):
        return [], summary, "AI 응답 형식이 예상과 다릅니다 (proposals 누락)."

    max_n = max(1, int(config.GOOGLE_AI_PROPOSAL_MAX_PER_RUN))
    valid: list[dict[str, Any]] = []
    for p in proposals[:max_n]:
        if not isinstance(p, dict):
            continue
        if p.get("action_type") not in _GOOGLE_ACTION_TYPES:
            continue
        p["raw_gemini_json"] = json.dumps(p, ensure_ascii=False)
        valid.append(p)

    return valid, summary, None
