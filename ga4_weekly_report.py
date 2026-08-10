"""
GA4 주간 유입 채널·이벤트 리포트 (지난 완료 주 vs 그 전 주, 월~일).
DB 스냅샷(ga4_week_channel / ga4_week_event) 기준 — google_weekly_report.py와 동일한 형식.
"""
from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd

import database as db

# 이벤트 이름 -> 화면에 보여줄 한글 라벨
EVENT_LABELS: dict[str, str] = {
    "lawtalk_inquiry_click": "로톡 상담 클릭",
    "kakao_inquiry_click": "카카오톡 문의 클릭",
    "specialty_click": "전문분야 클릭",
    "lawyer_intro_dwell_5s": "변호사 소개 5초 체류",
    "ai_counselor_send_click": "AI 상담 전송 클릭",
    "page_view": "페이지 조회",
    "session_start": "세션 시작",
    "first_visit": "첫 방문",
    "user_engagement": "사용자 참여",
    "scroll": "스크롤",
    "click": "클릭(일반)",
}

# 페이지 경로 -> 화면에 보여줄 한글 라벨 (대시보드가 별도 추적하는 주요 페이지)
PAGE_LABELS: dict[str, str] = {
    "/AIcounselor": "상담안내AI (AI 상담 챗봇)",
}

# 채널 이름 -> 화면에 보여줄 한글 라벨 (GA4 기본 채널 그룹은 영어로 내려옴)
CHANNEL_LABELS: dict[str, str] = {
    "Paid Search": "유료 검색 (구글 광고)",
    "Organic Search": "자연 검색",
    "Direct": "직접 접속",
    "Referral": "다른 사이트 경유",
    "Organic Social": "소셜(자연)",
    "Paid Social": "소셜(유료)",
    "Email": "이메일",
    "Display": "디스플레이 광고",
    "Cross-network": "교차 네트워크 (PMax 등)",
    "Unassigned": "미분류",
}

# 채널·이벤트·지표 — 업무 해석 (대시보드 용어집·자동 코멘트용)
CHANNEL_DESCRIPTIONS: dict[str, str] = {
    "Paid Search": "검색 결과 위쪽의 광고를 눌러 들어온 방문입니다. 구글 검색광고와 네이버 검색광고 등이 여기에 들어갑니다.",
    "Organic Search": "광고가 아닌 일반 검색 결과를 눌러 들어온 방문입니다. 사람들이 검색으로 우리 글을 발견했다는 뜻입니다.",
    "Direct": "GA4가 어디에서 왔는지 알 수 없는 방문입니다. 주소를 직접 입력하거나 즐겨찾기로 들어온 경우도 포함됩니다.",
    "Referral": "다른 홈페이지나 블로그에 있는 링크를 눌러 들어온 방문입니다.",
    "Organic Social": "돈을 내지 않은 SNS 게시글이나 커뮤니티 글을 통해 들어온 방문입니다.",
    "Paid Social": "인스타그램, 페이스북, 유튜브 같은 SNS 광고를 눌러 들어온 방문입니다.",
    "Email": "이메일 안에 있는 링크를 눌러 들어온 방문입니다.",
    "Display": "웹사이트나 앱에 보이는 그림·배너 광고를 눌러 들어온 방문입니다.",
    "Cross-network": "Performance Max처럼 구글의 검색·유튜브·배너 등 여러 광고 장소를 한꺼번에 사용하는 캠페인에서 들어온 방문입니다.",
    "Unassigned": "들어온 길에 대한 정보가 부족해서 GA4가 어느 채널인지 정하지 못한 방문입니다. 링크의 UTM이나 광고 연결 설정을 확인해야 합니다.",
    "AI Assistant": "ChatGPT 같은 AI 도우미가 보여 준 링크를 눌러 들어온 방문입니다.",
}

EVENT_DESCRIPTIONS: dict[str, str] = {
    "lawtalk_inquiry_click": "로톡 상담 버튼을 누른 횟수입니다. 상담에 관심을 보인 행동입니다.",
    "kakao_inquiry_click": "카카오톡 문의 버튼을 누른 횟수입니다. 바로 상담하고 싶어 하는 행동입니다.",
    "specialty_click": "형사·이혼 같은 전문분야 메뉴나 페이지를 누른 횟수입니다.",
    "lawyer_intro_dwell_5s": "변호사 소개 부분을 5초 이상 읽은 횟수입니다. 소개 내용을 관심 있게 본 행동입니다.",
    "ai_counselor_send_click": "AI 상담창에 내용을 적고 전송 버튼을 누른 횟수입니다. AI 상담을 실제로 사용한 행동입니다.",
    "page_view": "페이지가 화면에 열린 횟수입니다. 같은 사람이 여러 번 열면 여러 번 셉니다.",
    "session_start": "새로운 방문이 시작된 횟수입니다.",
    "first_visit": "그 기기와 브라우저에서 처음 방문한 횟수입니다.",
    "user_engagement": "페이지를 어느 정도 읽거나 중요한 행동을 해서 관심 있는 방문으로 인정된 횟수입니다.",
    "scroll": "페이지를 아래로 많이 내려 읽은 횟수입니다.",
    "click": "GA4가 자동으로 기록한 일반 링크 클릭 횟수입니다.",
}

METRIC_GLOSSARY: dict[str, str] = {
    "sessions": "방문 횟수입니다. 한 사람이 아침과 저녁에 다시 오면 보통 두 번으로 셉니다.",
    "active_users": "이 기간에 사이트를 이용한 사람 수입니다. 같은 사람의 반복 방문은 가능한 한 한 명으로 셉니다.",
    "engaged_sessions": "10초 이상 머물거나, 중요한 행동을 하거나, 여러 페이지를 본 방문입니다.",
    "session_share_pct": "전체 방문 중 이 채널이 차지한 비율입니다.",
    "event_count": "버튼 클릭이나 페이지 조회 같은 행동이 일어난 횟수입니다.",
}

def _snapshot_row(week_start: str) -> dict[str, Any] | None:
    for r in db.fetch_ga4_week_snapshots():
        if str(r["week_start"]) == week_start:
            return dict(r)
    return None


def has_snapshot(week_start: str) -> bool:
    return _snapshot_row(week_start) is not None


def week_totals(week_start: str) -> dict[str, Any]:
    snap = _snapshot_row(week_start)
    if not snap:
        return {"sessions": 0, "active_users": 0, "has_snapshot": False}
    return {
        "sessions": int(snap.get("total_sessions") or 0),
        "active_users": int(snap.get("total_active_users") or 0),
        "has_snapshot": True,
        "synced_at": snap.get("synced_at"),
    }


def channel_df(week_start: str) -> pd.DataFrame:
    """선택 주 — 채널별 세션·활성 사용자·비중(%)."""
    rows = [dict(r) for r in db.fetch_ga4_week_channel(week_start)]
    cols = ["channel", "label", "sessions", "active_users", "engaged_sessions", "session_share_pct"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["sessions"] = pd.to_numeric(df.get("sessions", 0), errors="coerce").fillna(0).astype(int)
    df["active_users"] = pd.to_numeric(df.get("active_users", 0), errors="coerce").fillna(0).astype(int)
    df["engaged_sessions"] = pd.to_numeric(df.get("engaged_sessions", 0), errors="coerce").fillna(0).astype(int)
    df["label"] = df["channel"].map(lambda c: CHANNEL_LABELS.get(c, c))
    total = float(df["sessions"].sum())
    df["session_share_pct"] = df["sessions"].apply(
        lambda s: (100.0 * float(s) / total) if total > 0 else 0.0
    )
    return df.sort_values("sessions", ascending=False).reset_index(drop=True)


def events_df(week_start: str) -> pd.DataFrame:
    """선택 주 — GA4에 수집된 전체 이벤트 이름별 발생 수."""
    rows = [dict(r) for r in db.fetch_ga4_week_event(week_start)]
    cols = ["event_name", "label", "event_count"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["event_count"] = pd.to_numeric(df.get("event_count", 0), errors="coerce").fillna(0).astype(int)
    df["label"] = df["event_name"].map(lambda n: EVENT_LABELS.get(n, n))
    return df.sort_values("event_count", ascending=False).reset_index(drop=True)


def page_df(week_start: str) -> pd.DataFrame:
    """선택 주 — 대시보드가 추적하는 주요 페이지별 조회수·사용자·평균 참여 시간."""
    rows = [dict(r) for r in db.fetch_ga4_week_page(week_start)]
    cols = ["page_path", "label", "views", "active_users", "avg_engagement_sec"]
    if not rows:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame(rows)
    df["views"] = pd.to_numeric(df.get("views", 0), errors="coerce").fillna(0).astype(int)
    df["active_users"] = pd.to_numeric(df.get("active_users", 0), errors="coerce").fillna(0).astype(int)
    df["avg_engagement_sec"] = pd.to_numeric(df.get("avg_engagement_sec", 0), errors="coerce").fillna(0.0)
    df["label"] = df["page_path"].map(lambda p: PAGE_LABELS.get(p, p))
    return df.sort_values("views", ascending=False).reset_index(drop=True)


def ai_counselor_usage(week_curr: str, week_prev: str) -> dict[str, Any]:
    """상담안내AI(AIcounselor) 페이지 조회수 + AI 상담 전송 클릭을 결합한 사용 현황.

    페이지 조회 = 페이지에 들어와 열람(진입)한 횟수.
    전송 클릭 = 실제로 채팅 입력창에 메시지를 써서 전송 버튼을 누른 횟수(=진짜 사용).
    두 수치를 함께 봐야 "들어오기만 했는지" vs "실제로 썼는지"를 구분할 수 있습니다.
    """
    from ga4_weekly_views import AI_COUNSELOR_PAGE_PATH

    def _page_row(week: str) -> dict[str, Any]:
        pdf = page_df(week)
        if pdf.empty:
            return {"views": 0, "active_users": 0, "avg_engagement_sec": 0.0}
        match = pdf[pdf["page_path"] == AI_COUNSELOR_PAGE_PATH]
        if match.empty:
            return {"views": 0, "active_users": 0, "avg_engagement_sec": 0.0}
        r = match.iloc[0]
        return {
            "views": int(r["views"]),
            "active_users": int(r["active_users"]),
            "avg_engagement_sec": float(r["avg_engagement_sec"]),
        }

    def _click_count(week: str) -> int:
        ev_map = {r["event_name"]: int(r.get("event_count") or 0) for r in [dict(x) for x in db.fetch_ga4_week_event(week)]}
        return ev_map.get("ai_counselor_send_click", 0)

    pc = _page_row(week_curr)
    pp = _page_row(week_prev)
    cc = _click_count(week_curr)
    cp = _click_count(week_prev)

    def _rate(clicks: int, views: int) -> float | None:
        return (100.0 * clicks / views) if views > 0 else None

    return {
        "page_views_curr": pc["views"],
        "page_views_prev": pp["views"],
        "page_users_curr": pc["active_users"],
        "page_users_prev": pp["active_users"],
        "avg_engagement_sec_curr": pc["avg_engagement_sec"],
        "avg_engagement_sec_prev": pp["avg_engagement_sec"],
        "send_clicks_curr": cc,
        "send_clicks_prev": cp,
        "usage_rate_curr": _rate(cc, pc["views"]),
        "usage_rate_prev": _rate(cp, pp["views"]),
    }


def key_events_compare(week_curr: str, week_prev: str) -> pd.DataFrame:
    """대시보드가 추적하는 주요 전환 이벤트(KEY_EVENT_NAMES) — 해당 주 vs 그 전 주."""
    cur_map = {r["event_name"]: int(r.get("event_count") or 0) for r in [dict(x) for x in db.fetch_ga4_week_event(week_curr)]}
    prev_map = {r["event_name"]: int(r.get("event_count") or 0) for r in [dict(x) for x in db.fetch_ga4_week_event(week_prev)]}

    from ga4_weekly_views import KEY_EVENT_NAMES

    rows: list[dict[str, Any]] = []
    for name in KEY_EVENT_NAMES:
        c = cur_map.get(name, 0)
        p = prev_map.get(name, 0)
        rows.append(
            {
                "event_name": name,
                "label": EVENT_LABELS.get(name, name),
                "count_curr": c,
                "count_prev": p,
                "change": c - p,
            }
        )
    return pd.DataFrame(rows)


def channel_weekly_compare(week_curr: str, week_prev: str) -> pd.DataFrame:
    """채널별 해당 주 vs 그 전 주 — 세션 비교. 정렬: 해당 주 세션 내림차순."""
    curr_raw = {str(r["channel"]): dict(r) for r in db.fetch_ga4_week_channel(week_curr)}
    prev_raw = {str(r["channel"]): dict(r) for r in db.fetch_ga4_week_channel(week_prev)}
    all_channels = set(curr_raw.keys()) | set(prev_raw.keys())
    if not all_channels:
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for ch in all_channels:
        cr = curr_raw.get(ch, {})
        pr = prev_raw.get(ch, {})
        sess_c = int(cr.get("sessions") or 0)
        sess_p = int(pr.get("sessions") or 0)
        if sess_c <= 0 and sess_p <= 0:
            continue
        rows.append(
            {
                "channel": ch,
                "label": CHANNEL_LABELS.get(ch, ch),
                "sessions_curr": sess_c,
                "sessions_prev": sess_p,
                "session_change": sess_c - sess_p,
                "active_users_curr": int(cr.get("active_users") or 0),
                "active_users_prev": int(pr.get("active_users") or 0),
            }
        )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    return df.sort_values("sessions_curr", ascending=False).reset_index(drop=True)


def report_meta_week_pair() -> tuple[date, date, date, date]:
    """지난 완료 주(-1) vs 그 전 주(-2)."""
    import time_utils

    _, _, curr_mon, curr_sun = time_utils.calendar_week_bounds(-1)
    _, _, prev_mon, prev_sun = time_utils.calendar_week_bounds(-2)
    return prev_mon, prev_sun, curr_mon, curr_sun


def describe_channel(channel: str) -> str:
    return CHANNEL_DESCRIPTIONS.get(channel, f"GA4 기본 채널 `{channel}` — 상세 설명은 GA4 관리자에서 확인하세요.")


def describe_event(event_name: str) -> str:
    return EVENT_DESCRIPTIONS.get(
        event_name,
        f"커스텀/자동 이벤트 `{event_name}` — GTM·GA4 이벤트 목록에서 트리거를 확인하세요.",
    )


PAGE_DESCRIPTIONS: dict[str, str] = {
    "/AIcounselor": "GA4가 자동으로 센 상담안내AI 페이지 방문입니다. 진입만으로는 '사용'이라 보기 어렵고, "
    "실제 사용 여부는 `AI 상담 전송 클릭` 이벤트로 함께 확인해야 합니다.",
}


def describe_page(page_path: str) -> str:
    return PAGE_DESCRIPTIONS.get(
        page_path,
        f"페이지 경로 `{page_path}` — GA4 '페이지 및 화면' 보고서에서 상세를 확인하세요.",
    )


def glossary_dataframe() -> pd.DataFrame:
    """채널·주요 이벤트·지표 해석표."""
    rows: list[dict[str, str]] = []
    for ch, label in CHANNEL_LABELS.items():
        rows.append({"구분": "유입 채널", "이름": label, "GA4 코드": ch, "의미": describe_channel(ch)})
    for ev in list(EVENT_DESCRIPTIONS.keys()):
        rows.append(
            {
                "구분": "이벤트/태그",
                "이름": EVENT_LABELS.get(ev, ev),
                "GA4 코드": ev,
                "의미": describe_event(ev),
            }
        )
    for page, label in PAGE_LABELS.items():
        rows.append(
            {
                "구분": "페이지",
                "이름": label,
                "GA4 코드": page,
                "의미": describe_page(page),
            }
        )
    for key, desc in METRIC_GLOSSARY.items():
        rows.append({"구분": "지표", "이름": key, "GA4 코드": key, "의미": desc})
    return pd.DataFrame(rows)


def weekly_history_table(max_weeks: int = 12) -> pd.DataFrame:
    """DB에 저장된 주간 스냅샷 — 세션·사용자 추이."""
    import time_utils

    rows: list[dict[str, object]] = []
    for off in range(0, -max_weeks - 1, -1):
        _, _, mon, sun = time_utils.calendar_week_bounds(off)
        tot = week_totals(mon.isoformat())
        ch_df = channel_df(mon.isoformat())
        top_ch = ""
        if not ch_df.empty:
            top = ch_df.iloc[0]
            top_ch = f"{top['label']} ({int(top['sessions']):,})"
        rows.append(
            {
                "offset": off,
                "월~일": f"{mon} ~ {sun}",
                "세션": int(tot.get("sessions") or 0),
                "활성 사용자": int(tot.get("active_users") or 0),
                "1위 채널": top_ch or "–",
                "수집": "✓" if tot.get("has_snapshot") else "–",
            }
        )
    df = pd.DataFrame(rows)
    return df.sort_values("offset", ascending=False).reset_index(drop=True)


def _pct_change(curr: float, prev: float) -> str:
    if prev <= 0 and curr <= 0:
        return "변동 없음"
    if prev <= 0:
        return "전주 대비 신규(전주 0)"
    return f"{100.0 * (curr - prev) / prev:+.1f}%"


def build_homepage_analysis(
    prev_mon: date,
    prev_sun: date,
    curr_mon: date,
    curr_sun: date,
) -> list[str]:
    """홈페이지 GA4 — 전주 대비 해석 (마크다운 불릿). 표·차트 아래 배치용."""
    import time_utils

    lines: list[str] = []
    this_rng = time_utils.format_week_range(curr_mon, curr_sun)
    prev_rng = time_utils.format_week_range(prev_mon, prev_sun)
    lines.append(f"**비교 구간** — 해당 주 `{this_rng}` vs 그 전 주 `{prev_rng}`.")

    p_tot = week_totals(prev_mon.isoformat())
    c_tot = week_totals(curr_mon.isoformat())
    c_sess = int(c_tot.get("sessions") or 0)
    p_sess = int(p_tot.get("sessions") or 0)
    c_users = int(c_tot.get("active_users") or 0)
    p_users = int(p_tot.get("active_users") or 0)

    if c_sess <= 0 and p_sess <= 0:
        lines.append(
            "선택 구간에 GA4 세션이 없습니다. **홈페이지 동기화**로 과거 주를 수집하거나, "
            "GA4 속성에 실제로 데이터가 쌓인 시점(태그 설치일) 이후 주만 비교할 수 있습니다."
        )
        return lines

    # ── KPI 전주 비교 ──
    d_sess = c_sess - p_sess
    d_users = c_users - p_users
    if p_sess > 0:
        lines.append(
            f"**세션** 해당 주 **{c_sess:,}** · 그 전 주 **{p_sess:,}** "
            f"(**{d_sess:+,}**, {_pct_change(c_sess, p_sess)})."
        )
    else:
        lines.append(
            f"**세션** 해당 주 **{c_sess:,}**. "
            "그 전 주 세션이 0(또는 미수집)이라 증감률은 계산하지 않습니다 — "
            "태그 설치 직후·첫 수집 주일 수 있습니다."
        )
    if p_users > 0:
        lines.append(
            f"**활성 사용자** 해당 주 **{c_users:,}** · 그 전 주 **{p_users:,}** "
            f"(**{d_users:+,}**, {_pct_change(c_users, p_users)})."
        )
    else:
        lines.append(f"**활성 사용자** 해당 주 **{c_users:,}** (전주 비교 불가).")

    # ── 채널 전주 비교 ──
    ch = channel_weekly_compare(curr_mon.isoformat(), prev_mon.isoformat())
    if not ch.empty and c_sess > 0:
        top = ch.iloc[0]
        share = 100.0 * int(top["sessions_curr"]) / c_sess
        lines.append(
            f"**유입 1위** `{top['label']}` — 세션 **{int(top['sessions_curr']):,}** "
            f"(전체 **{share:.1f}%**), 전주 대비 **{int(top['session_change']):+,}**. "
            f"{describe_channel(str(top['channel']))}"
        )
        gainers = ch.sort_values("session_change", ascending=False)
        losers = ch.sort_values("session_change", ascending=True)
        g0 = gainers.iloc[0]
        if int(g0["session_change"]) > 0:
            lines.append(
                f"**전주 대비 증가 채널** `{g0['label']}` "
                f"**{int(g0['sessions_prev']):,} → {int(g0['sessions_curr']):,}** "
                f"({int(g0['session_change']):+,})."
            )
        l0 = losers.iloc[0]
        if int(l0["session_change"]) < 0:
            lines.append(
                f"**전주 대비 감소 채널** `{l0['label']}` "
                f"**{int(l0['sessions_prev']):,} → {int(l0['sessions_curr']):,}** "
                f"({int(l0['session_change']):+,})."
            )
        paid = ch[ch["channel"].isin(["Paid Search", "Cross-network", "Display", "Paid Social"])]
        organic = ch[ch["channel"].isin(["Organic Search", "Direct", "Organic Social", "Referral"])]
        paid_c = int(paid["sessions_curr"].sum()) if not paid.empty else 0
        paid_p = int(paid["sessions_prev"].sum()) if not paid.empty else 0
        org_c = int(organic["sessions_curr"].sum()) if not organic.empty else 0
        org_p = int(organic["sessions_prev"].sum()) if not organic.empty else 0
        if paid_c + org_c > 0:
            lines.append(
                f"**유료 vs 자연·직접** — 유료성 채널 세션 **{paid_c:,}** "
                f"(전주 {paid_p:,}, {paid_c - paid_p:+,}) · "
                f"자연·직접·소셜·리퍼럴 **{org_c:,}** "
                f"(전주 {org_p:,}, {org_c - org_p:+,})."
            )

    # ── 전환 이벤트 전주 비교 ──
    ev = key_events_compare(curr_mon.isoformat(), prev_mon.isoformat())
    if not ev.empty:
        conv_c = int(ev["count_curr"].sum())
        conv_p = int(ev["count_prev"].sum())
        if conv_c > 0 or conv_p > 0:
            lines.append(
                f"**주요 전환(GTM) 합계** 해당 주 **{conv_c}건** · 그 전 주 **{conv_p}건** "
                f"({conv_c - conv_p:+,})."
            )
            for _, r in ev.iterrows():
                cc, cp = int(r["count_curr"]), int(r["count_prev"])
                if cc == 0 and cp == 0:
                    continue
                lines.append(
                    f"· `{r['label']}` **{cp} → {cc}** ({cc - cp:+,}). "
                    f"{describe_event(str(r['event_name']))}"
                )
        else:
            lines.append(
                "**주요 전환(GTM)** 5종(로톡·카카오·전문분야·변호사 5초·AI 상담 전송)이 "
                "해당 주·전주 모두 **0건**입니다. 태그 게시·버튼 노출을 확인하세요."
            )

    # ── AI 상담안내(AIcounselor) 사용 현황 ──
    ai_usage = ai_counselor_usage(curr_mon.isoformat(), prev_mon.isoformat())
    pv_c, pv_p = ai_usage["page_views_curr"], ai_usage["page_views_prev"]
    sc_c, sc_p = ai_usage["send_clicks_curr"], ai_usage["send_clicks_prev"]
    if pv_c > 0 or pv_p > 0 or sc_c > 0 or sc_p > 0:
        rate_c = ai_usage["usage_rate_curr"]
        rate_txt = f"{rate_c:.0f}%" if rate_c is not None else "–"
        lines.append(
            f"**상담안내AI 사용 현황** 페이지 조회 **{pv_p} → {pv_c}**"
            f"({pv_c - pv_p:+,}) · 전송 클릭 **{sc_p} → {sc_c}**({sc_c - sc_p:+,}) · "
            f"조회 대비 실사용률 **{rate_txt}**. "
            "페이지에 들어온 것만으로는 '사용'이라 보기 어렵고, 전송 클릭이 있어야 실제로 채팅을 사용한 것입니다."
        )

    # ── 한 줄 총평 ──
    if p_sess <= 0 and c_sess > 0:
        lines.append(
            "**총평** — 전주 데이터가 비어 있어 '증가'로 보이기보다 **첫 유효 수집 주**로 보는 편이 맞습니다. "
            "다음 주부터 전주 대비 증감이 의미 있게 비교됩니다."
        )
    elif d_sess > 0 and p_sess > 0:
        lines.append(
            f"**총평** — 전주 대비 세션이 **{d_sess:+,}** 늘었습니다. "
            "유입 1위 채널과 전환 이벤트 증감을 함께 보면 광고·콘텐츠 효과를 가늠할 수 있습니다."
        )
    elif d_sess < 0 and p_sess > 0:
        lines.append(
            f"**총평** — 전주 대비 세션이 **{d_sess:+,}** 줄었습니다. "
            "감소가 큰 채널(유료/자연)을 우선 점검하세요."
        )
    elif p_sess > 0:
        lines.append("**총평** — 전주와 세션 규모가 비슷합니다. 채널 믹스·전환 건수 변화를 보세요.")

    hist = weekly_history_table(max_weeks=16)
    with_data = hist[hist["세션"] > 0]
    if not with_data.empty and len(with_data) == 1:
        first = with_data.iloc[-1]["월~일"]
        lines.append(
            f"참고: GA4에 세션이 있는 주는 **{first}**부터입니다. "
            "그 이전 주는 설치 전이라 0일 수 있습니다."
        )

    return lines
