"""
Integrated dashboard: ads + traffic merge view.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import streamlit as st
import case_admin
import winning_case_admin
import streamlit.components.v1 as components

import charts
import config
import database as db
import ga4_weekly_report
import ga4_weekly_views
import gemini_weekly_analysis
import google_weekly_report
import google_weekly_views
import google_ads_actions
import homepage_content
import merge
import naver_weekly_report
import naver_weekly_views
import pdf_report
import sync_job
import time_utils
from integrations.ga4_client import (
    fetch_ga4_content_week,
    fetch_ga4_daily_traffic,
    fetch_ga4_search_source_week,
    fetch_ga4_unassigned_detail_week,
)

st.set_page_config(
    page_title="주간 광고 결과",
    layout="wide",
    initial_sidebar_state="collapsed",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": None,
    },
)

# 왼쪽 메뉴: 홈(전체) / 네이버 / 구글 / 홈페이지
_PAGE_DEF: list[tuple[str, str]] = [
    ("home", "홈"),
    ("naver", "네이버"),
    ("google", "구글"),
    ("homepage", "홈페이지"),
    ("case_admin", "상담사례 등록"),
    ("winning_case_admin", "승소사례 등록"),
]
_PAGE_LABELS = [b for _, b in _PAGE_DEF]
_PAGE_ID_BY_LABEL = {b: a for a, b in _PAGE_DEF}
# None = 전체 섹션 표시 (홈)
_PAGE_SECTIONS: dict[str, set[str] | None] = {
    "home": None,
    "naver": {"naver_week_report", "naver_weekly", "campaign_notes"},
    "google": {"google_week_report"},
    "homepage": {"ga4_traffic", "spend_sessions", "today_snap"},
}

# 사이드바 multiselect 레이블 ↔ 내부 id
_SECTION_DEF: list[tuple[str, str]] = [
    ("hero", "① 주간 브리핑 (지난 완료 주 vs 그 전 주)"),
    ("naver_week_report", "② 네이버 주간 성과 비교표"),
    ("gemini_ai", "③ AI 주간 분석 (Gemini)"),
    ("selected_kpi", "④ 선택 주 · 통합 광고 KPI"),
    ("weekly_trend", "⑤ 주간 추이 (클릭·비용)"),
    ("today_snap", "⑥ 오늘 스냅샷"),
    ("naver_weekly", "⑦ 네이버 · 선택 주 상세"),
    ("spend_sessions", "⑧ 광고비·세션 차트"),
    ("campaign_notes", "⑨ 캠페인별 특이사항"),
    ("alerts", "⑩ 알림 로그"),
    ("google_week_report", "⑪ 구글 주간 성과 비교표"),
    ("ga4_traffic", "⑬ 홈페이지 · GA4 (유입·전환 이벤트)"),
]
_SECTION_LABELS = [b for _, b in _SECTION_DEF]
_SECTION_ID_BY_LABEL = {b: a for a, b in _SECTION_DEF}


def _current_page() -> str:
    label = st.session_state.get("dash_page_pick")
    if label in _PAGE_ID_BY_LABEL:
        return _PAGE_ID_BY_LABEL[label]
    return "home"


def _dash_active_section_ids() -> set[str]:
    raw = st.session_state.get("dash_section_pick")
    if not raw:
        return {a for a, _ in _SECTION_DEF}
    return {_SECTION_ID_BY_LABEL[x] for x in raw if x in _SECTION_ID_BY_LABEL}


def _show_section(sid: str) -> bool:
    page = _current_page()
    allowed = _PAGE_SECTIONS.get(page)
    if allowed is not None and sid not in allowed:
        return False
    return sid in _dash_active_section_ids()


def _wk(base: str) -> str:
    """위젯 key — fragment 제거 후 안정적인 key 사용."""
    return base


@st.dialog("한 달 일별 방문자", width="large")
def _show_monthly_visitors_dialog(rows: list[dict], month_label: str) -> None:
    st.caption(f"{month_label}의 날짜별 방문자 수입니다. 세션은 방문 횟수, 조회수는 페이지가 열린 횟수입니다.")
    if not rows:
        st.info("이 달에는 불러온 방문자 데이터가 없습니다.")
        return
    daily = pd.DataFrame(rows)
    daily["날짜"] = pd.to_datetime(daily["date"]).dt.strftime("%m월 %d일")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=daily["날짜"],
            y=daily["active_users"],
            name="방문자 수",
            mode="lines+markers",
            line={"color": "#1a73e8", "width": 3},
        )
    )
    fig.update_layout(
        yaxis_title="명",
        xaxis_title="날짜",
        hovermode="x unified",
        margin={"l": 20, "r": 20, "t": 20, "b": 20},
    )
    st.plotly_chart(fig, use_container_width=True, config={"responsive": True})
    table = daily.rename(
        columns={
            "weekday": "요일",
            "active_users": "방문자 수",
            "sessions": "세션",
            "page_views": "페이지 조회수",
        }
    )
    st.dataframe(
        table[["날짜", "요일", "방문자 수", "세션", "페이지 조회수"]],
        use_container_width=True,
        hide_index=True,
    )

def _inject_print_css() -> None:
    """인쇄 시 스크롤·높이 제한 해제, 사이드바·버튼 숨김, 표 전체 출력."""
    st.markdown(
        """
<style>
@media print {
  [data-testid="stSidebar"],
  [data-testid="stToolbar"],
  [data-testid="stHeader"],
  [data-testid="stStatusWidget"],
  footer,
  .stButton,
  .stCheckbox,
  .stDownloadButton,
  [data-testid="stMultiSelect"],
  [data-testid="stSelectbox"],
  [data-testid="stChatInput"],
  iframe[title="streamlit_components_v1"] {
    display: none !important;
  }
  [data-testid="stDataFrame"],
  [data-testid="stDataFrameResizable"],
  [data-testid="glideDataEditor"],
  [data-testid="stTable"],
  section.main > div {
    max-height: none !important;
    height: auto !important;
    overflow: visible !important;
  }
  [data-testid="stDataFrame"] > div,
  [data-testid="stDataFrame"] iframe {
    max-height: none !important;
    overflow: visible !important;
  }
  [data-testid="stHorizontalBlock"] {
    flex-direction: column !important;
    gap: 0.75rem !important;
  }
  [data-testid="column"] {
    width: 100% !important;
    flex: 1 1 100% !important;
    max-width: 100% !important;
  }
  [data-testid="stExpanderDetails"] {
    display: block !important;
    visibility: visible !important;
    height: auto !important;
  }
  h2, h3, h4 { page-break-after: avoid; }
  [data-testid="stTable"] { page-break-inside: avoid; }
  body { print-color-adjust: exact; -webkit-print-color-adjust: exact; }
}
</style>
        """,
        unsafe_allow_html=True,
    )


def _print_button() -> None:
    components.html(
        """
<button id="dashPrintBtn" style="
  width:100%; padding:0.45rem 0.6rem; border-radius:0.45rem;
  border:1px solid #ccc; background:#f8f9fa; cursor:pointer;
  font-size:14px; font-family:inherit;
">🖨️ 인쇄</button>
<script>
document.getElementById('dashPrintBtn').onclick = function() {
  try { window.parent.print(); } catch (e) { window.print(); }
};
</script>
        """,
        height=44,
    )


def _weekly_trend_table(merged_all: pd.DataFrame, max_weeks: int = 12) -> pd.DataFrame:
    """월~일 완료 주 단위로 통합 광고 KPI (offset 오래된 것 → 최근 순)."""
    rows: list[dict[str, object]] = []
    lookback = max(1, max_weeks)
    for off in range(-lookback, 1):
        w_start, w_end, d0, d1 = time_utils.calendar_week_bounds(off)
        if merged_all.empty:
            m = merged_all
        else:
            m = merged_all[
                (merged_all["ts_hour"] >= w_start) & (merged_all["ts_hour"] <= w_end)
            ].copy()
        wap = merge.week_ad_performance(m, week_start=d0.isoformat())
        pt = merge.period_totals(m)
        rows.append(
            {
                "월~일": f"{d0} ~ {d1}",
                "offset": off,
                "통합 클릭": int(round(wap["total_clicks"])),
                "네이버 클릭": int(round(wap["naver_clicks"])),
                "구글 클릭": int(round(wap["google_clicks"])),
                "통합 비용(원)": round(wap["total_cost"], 0),
                "통합 노출": int(round(wap["total_impressions"])),
                "CTR(%)": round(wap["blended_ctr_pct"], 2),
                "블렌드 CPC": round(wap["blended_cpc"], 0),
                "ROAS": round(pt["roas"], 2),
            }
        )
    return pd.DataFrame(rows).sort_values("offset").reset_index(drop=True)


def _render_scope_status(merged_all: pd.DataFrame, ts_start: str, ts_end: str, week_off: int) -> None:
    """현재 화면이 다루는 기간과 네이버 스냅샷 커버리지를 명확히 노출."""
    _, _, h_mon, h_sun = time_utils.calendar_week_bounds(-1)
    _, _, p_mon, p_sun = time_utils.calendar_week_bounds(-2)
    _, _, s_mon, s_sun = time_utils.calendar_week_bounds(week_off)
    with st.container(border=True):
        st.markdown("#### 범위 · 연동 상태")
        st.markdown(
            f"**히어로 비교** — {time_utils.format_week_range(h_mon, h_sun)} "
            f"vs {time_utils.format_week_range(p_mon, p_sun)}"
        )
        st.markdown(
            f"**선택 주 (사이드바)** — {time_utils.format_week_range(s_mon, s_sun)}"
        )
        st.caption(f"시간별 병합 로드: `{ts_start}` ~ `{ts_end}`")

        if merged_all.empty:
            st.warning("시간별 병합 데이터가 비어 있습니다. 「지금 동기화 실행」 후 다시 확인하세요.")
        else:
            st.success("시간별 병합 데이터가 로드되었습니다.")

        snaps = db.fetch_naver_week_snapshots()
        if not snaps:
            st.warning("네이버 주간 스냅샷이 없습니다. 사이드바 「네이버 주간 스냅샷 수집」이 필요합니다.")
            return

        sdf = pd.DataFrame([dict(s) for s in snaps]).sort_values("week_start")
        oldest = str(sdf.iloc[0]["week_start"])
        newest = str(sdf.iloc[-1]["week_start"])
        latest_sync = str(sdf.iloc[-1]["synced_at"])
        st.caption(
            f"네이버 주간 스냅샷 보유 주차: `{oldest}` ~ `{newest}` (week_start 기준) · 최근 수집시각: `{latest_sync}`"
        )

        selected_week_start = s_mon.isoformat()
        if selected_week_start in set(sdf["week_start"].astype(str)):
            st.success(f"선택 주 `{selected_week_start}` 네이버 스냅샷이 존재합니다.")
        else:
            st.warning(
                f"선택 주 `{selected_week_start}` 네이버 스냅샷이 없습니다. "
                "네이버 API 주간 수집 범위를 늘리거나 재수집하세요."
            )


def _campaign_special_notes(campaign_df: pd.DataFrame) -> list[str]:
    """선택 주 네이버 캠페인별 특이사항 자동 요약."""
    if campaign_df.empty:
        return ["캠페인별 데이터가 없어 특이사항을 계산하지 못했습니다."]

    cdf = campaign_df.copy()
    cdf["clicks"] = pd.to_numeric(cdf.get("clicks", 0), errors="coerce").fillna(0)
    cdf["impressions"] = pd.to_numeric(cdf.get("impressions", 0), errors="coerce").fillna(0)
    cdf["ctr_pct"] = cdf.apply(
        lambda r: (100.0 * float(r["clicks"]) / float(r["impressions"])) if float(r["impressions"]) > 0 else 0.0,
        axis=1,
    )
    cdf["campaign_name"] = cdf.get("campaign_name", "").fillna("").astype(str)
    notes: list[str] = []

    total_clicks = float(cdf["clicks"].sum())
    top = cdf.sort_values(["clicks", "impressions"], ascending=False).iloc[0]
    top_name = top["campaign_name"] or "(이름 없음)"
    top_clicks = float(top["clicks"])
    top_share = (100.0 * top_clicks / total_clicks) if total_clicks > 0 else 0.0
    if total_clicks > 0:
        notes.append(f"클릭 상위 캠페인: `{top_name}` {int(top_clicks):,}클릭 (전체의 {top_share:.1f}%).")
    else:
        notes.append("선택 주 캠페인 총 클릭이 0이라 집중도를 계산할 수 없습니다.")

    cdf_hi_imp = cdf[cdf["impressions"] >= 300].copy()
    if not cdf_hi_imp.empty:
        zero_clk = cdf_hi_imp[cdf_hi_imp["clicks"] <= 0]
        if not zero_clk.empty:
            z = zero_clk.iloc[0]
            zname = z["campaign_name"] or "(이름 없음)"
            notes.append(
                f"노출 300+인데 클릭 0인 캠페인 존재: `{zname}` "
                f"(노출 {int(z['impressions']):,}). 소재/키워드 점검이 필요합니다."
            )

    cdf_for_ctr = cdf[cdf["impressions"] >= 200].copy()
    if len(cdf_for_ctr) >= 2:
        ctr_med = float(cdf_for_ctr["ctr_pct"].median())
        weak = cdf_for_ctr[cdf_for_ctr["ctr_pct"] < (ctr_med * 0.5)]
        if not weak.empty and ctr_med > 0:
            w = weak.sort_values("ctr_pct").iloc[0]
            wname = w["campaign_name"] or "(이름 없음)"
            notes.append(
                f"CTR 저효율 후보: `{wname}` CTR {float(w['ctr_pct']):.2f}% "
                f"(동주 중앙값 {ctr_med:.2f}% 대비 낮음)."
            )

        strong = cdf_for_ctr[cdf_for_ctr["ctr_pct"] > (ctr_med * 1.5)]
        if not strong.empty and ctr_med > 0:
            s = strong.sort_values(["ctr_pct", "clicks"], ascending=False).iloc[0]
            sname = s["campaign_name"] or "(이름 없음)"
            notes.append(
                f"CTR 우수 후보: `{sname}` CTR {float(s['ctr_pct']):.2f}% "
                f"(중앙값 {ctr_med:.2f}% 대비 높음)."
            )

    return notes[:5]


def _dash_week_lookups() -> tuple[list[str], dict[str, int]]:
    """주차 목록: 맨 위가 **지난 완료 월~일(-1)**, 그다음이 전전주…, 맨 아래가 **이번 주(0)**."""
    lookback = max(7, int(config.SYNC_LOOKBACK_DAYS))
    max_weeks = max(1, lookback // 7)
    past_offsets = list(range(-1, -max_weeks - 1, -1))
    week_offsets = past_offsets + [0]
    week_labels = [time_utils.week_label(o) for o in week_offsets]
    label_to_offset = dict(zip(week_labels, week_offsets))
    return week_labels, label_to_offset


def check_login(user: str, password: str) -> bool:
    u_in = (user or "").strip().replace("\r", "")
    p_in = (password or "").strip().replace("\r", "")
    eu, ep = config.get_dashboard_credentials()
    return u_in == eu and p_in == ep


def login_form() -> None:
    st.title("로그인")
    st.caption("계정은 `.env`의 `DASHBOARD_USER`, `DASHBOARD_PASSWORD` 입니다.")

    if "login_error" not in st.session_state:
        st.session_state["login_error"] = ""

    eu, ep = config.get_dashboard_credentials()
    st.caption("관리자 계정으로 로그인해 주세요.")

    uid = st.text_input("사용자 ID", value=eu)
    pw = st.text_input("비밀번호", type="password")

    if st.button("로그인", type="primary", use_container_width=True):
        if check_login(uid, pw):
            st.session_state["logged_in"] = True
            st.session_state["login_error"] = ""
            st.rerun()
        else:
            st.session_state["login_error"] = "ID 또는 비밀번호가 올바르지 않습니다."

    if st.session_state.get("login_error"):
        st.error(st.session_state["login_error"])


def plotly_responsive(fig: go.Figure) -> None:
    fig.update_layout(
        autosize=True,
        margin=dict(l=40, r=20, t=40, b=40),
        legend_orientation="h",
        legend_yanchor="bottom",
        legend_y=1.02,
    )
    st.plotly_chart(fig, use_container_width=True, config={"responsive": True})


def _summarize_week_rows(rows: list) -> tuple[int, int, int]:
    """주간 sync 결과 → (신규 성공, DB 재사용, 실패) 건수."""
    ok_rows = [r for r in rows if isinstance(r, dict) and bool(r.get("ok"))]
    cached = [r for r in rows if isinstance(r, dict) and r.get("cached")]
    err_rows = [r for r in rows if isinstance(r, dict) and ((not bool(r.get("ok"))) or r.get("error"))]
    new_ok = max(0, len(ok_rows) - len(cached))
    return new_ok, len(cached), len(err_rows)


def _sync_hourly_step(*, force: bool, summary: dict[str, object]) -> None:
    mode_label = "전체 강제(과거 포함)" if force else "증분(최근만·DB 재사용)"
    st.write(f"광고·트래픽 시간별 데이터 동기화 중… ({mode_label})")
    try:
        hourly = sync_job.run_sync(force=force)
        summary["hourly"] = hourly
        st.write(
            f"　→ 시간별 {int(hourly.get('ad_points', 0)):,}건 · "
            f"{hourly.get('days')}일 · mode={hourly.get('mode')}"
        )
    except Exception as exc:  # noqa: BLE001
        summary["hourly_error"] = str(exc)
        st.write(f"　→ 시간별 동기화 오류: {exc}")


def _sync_naver_step(*, weeks: int, force: bool, summary: dict[str, object]) -> None:
    st.write(f"네이버 주간 스냅샷·키워드 수집 중… ({'전체 강제' if force else '신규 주만 · DB 캐시'})")
    prog = st.progress(0.0, text="네이버 주간 준비 중…")

    def _on_progress(step: int, total: int, label: str) -> None:
        frac = step / max(1, total)
        prog.progress(min(1.0, frac), text=f"네이버 주간 {step}/{total} · {label}")

    try:
        rows = naver_weekly_views.sync_naver_weekly_back(
            weeks, skip_existing=not force, progress=_on_progress
        )
        prog.progress(1.0, text="네이버 주간 완료")
        summary["weekly"] = rows
        new_ok, cached_n, err_n = _summarize_week_rows(rows)
        st.write(f"　→ 신규 {new_ok}주 · DB 재사용 {cached_n}주 · 실패 {err_n}주")
    except Exception as exc:  # noqa: BLE001
        summary["weekly_error"] = str(exc)
        st.write(f"　→ 네이버 주간 수집 오류: {exc}")


def _sync_google_step(*, weeks: int, force: bool, summary: dict[str, object]) -> None:
    g_weeks = max(weeks, max(1, int(config.GOOGLE_WEEKLY_BACK_WEEKS)))
    st.write(f"구글 주간 캠페인·키워드 수집 중… ({'전체 강제' if force else '신규 주만 · DB 캐시'})")
    gprog = st.progress(0.0, text="구글 주간 준비 중…")

    def _on_g_progress(step: int, total: int, label: str) -> None:
        frac = step / max(1, total)
        gprog.progress(min(1.0, frac), text=f"구글 주간 {step}/{total} · {label}")

    try:
        grows = google_weekly_views.sync_google_weekly_back(
            g_weeks, skip_existing=not force, progress=_on_g_progress
        )
        gprog.progress(1.0, text="구글 주간 완료")
        summary["google_weekly"] = grows
        new_ok, cached_n, err_n = _summarize_week_rows(grows)
        st.write(f"　→ 신규 {new_ok}주 · DB 재사용 {cached_n}주 · 실패 {err_n}주")
    except Exception as exc:  # noqa: BLE001
        summary["google_weekly_error"] = str(exc)
        st.write(f"　→ 구글 주간 수집 오류: {exc}")


def _sync_ga4_step(*, weeks: int, force: bool, summary: dict[str, object]) -> None:
    st.write(f"홈페이지 GA4 유입 채널·이벤트 수집 중… ({'전체 강제' if force else '신규 주만 · DB 캐시'})")
    if not config.GA4_PROPERTY_ID:
        st.write("　→ GA4_PROPERTY_ID 미설정 — 건너뜀 (.env 확인)")
        return
    gaprog = st.progress(0.0, text="GA4 주간 준비 중…")

    def _on_ga_progress(step: int, total: int, label: str) -> None:
        frac = step / max(1, total)
        gaprog.progress(min(1.0, frac), text=f"GA4 주간 {step}/{total} · {label}")

    try:
        garows = ga4_weekly_views.sync_ga4_weekly_back(
            max(weeks, max(1, int(config.GA4_WEEKLY_BACK_WEEKS))),
            skip_existing=not force,
            progress=_on_ga_progress,
        )
        gaprog.progress(1.0, text="GA4 주간 완료")
        summary["ga4_weekly"] = garows
        new_ok, cached_n, err_n = _summarize_week_rows(garows)
        st.write(f"　→ 신규 {new_ok}주 · DB 재사용 {cached_n}주 · 실패 {err_n}주")
    except Exception as exc:  # noqa: BLE001
        summary["ga4_weekly_error"] = str(exc)
        st.write(f"　→ GA4 주간 수집 오류: {exc}")


def _run_channel_sync(channel: str, *, weeks: int, force: bool) -> dict[str, object]:
    """
    channel:
      - all: 시간별 + 네이버 + 구글 + 홈페이지(GA4)
      - naver: 네이버 주간 (+ 시간별 광고)
      - google: 구글 주간 (+ 시간별 광고)
      - homepage: 홈페이지 GA4 (+ 시간별 트래픽)
    """
    labels = {
        "all": "전체 동기화",
        "naver": "네이버 동기화",
        "google": "구글 동기화",
        "homepage": "홈페이지 동기화",
    }
    title = labels.get(channel, "동기화")
    summary: dict[str, object] = {"channel": channel}
    with st.status(f"{title} 진행 중…", expanded=True) as status:
        if channel in ("all", "naver", "google", "homepage"):
            # 시간별: 광고(네이버/구글) + 트래픽(홈페이지) — 해당 채널에서도 같이 갱신
            _sync_hourly_step(force=force, summary=summary)
        if channel in ("all", "naver"):
            _sync_naver_step(weeks=weeks, force=force, summary=summary)
        if channel in ("all", "google"):
            _sync_google_step(weeks=weeks, force=force, summary=summary)
        if channel in ("all", "homepage"):
            _sync_ga4_step(weeks=weeks, force=force, summary=summary)

        has_err = bool(
            summary.get("hourly_error")
            or summary.get("weekly_error")
            or summary.get("google_weekly_error")
            or summary.get("ga4_weekly_error")
        )
        week_errs = 0
        for key in ("weekly", "google_weekly", "ga4_weekly"):
            rows = summary.get(key) or []
            week_errs += sum(
                1
                for r in rows
                if isinstance(r, dict) and ((not r.get("ok")) or r.get("error"))
            )
        if has_err or week_errs:
            status.update(label=f"{title} 완료 (일부 경고 있음)", state="error")
        else:
            status.update(label=f"✅ {title} 완료", state="complete")
    return summary


def _run_full_sync(*, weeks: int, force: bool) -> dict[str, object]:
    """하위 호환 — 전체 동기화."""
    return _run_channel_sync("all", weeks=weeks, force=force)


def _render_main_toolbar() -> None:
    """오른쪽 상단 — 페이지별 동기화 + PDF 보고서."""
    page = _current_page()
    sync_cfg = {
        "home": ("all", "🔄 전체 동기화", "시간별 · 네이버 · 구글 · 홈페이지"),
        "naver": ("naver", "🔄 네이버 동기화", "시간별 광고 · 네이버 주간"),
        "google": ("google", "🔄 구글 동기화", "시간별 광고 · 구글 주간"),
        "homepage": ("homepage", "🔄 홈페이지 동기화", "시간별 트래픽 · GA4 주간"),
    }
    channel, sync_label, sync_hint = sync_cfg.get(page, sync_cfg["home"])

    col_title, col_opt, col_sync, col_pdf = st.columns([4, 2, 2, 2])
    with col_title:
        st.title("주간 광고 결과")
        st.caption("화면은 SQLite DB만 읽습니다. 메뉴별로 해당 채널만 동기화할 수 있습니다.")
    with col_opt:
        force = st.checkbox(
            "강제 새로고침",
            value=False,
            key=_wk("toolbar_force"),
            help=(
                "끄면(기본): 이미 DB에 있는 과거 주·오래된 시간대는 API를 다시 부르지 않습니다. "
                "켜면: 선택 채널의 전체 기간을 API로 다시 받아 덮어씁니다."
            ),
        )
        st.caption(sync_hint)
    with col_sync:
        weeks = max(1, int(config.NAVER_WEEKLY_BACK_WEEKS))
        clicked = st.button(
            sync_label,
            type="primary",
            use_container_width=True,
            key=_wk(f"toolbar_sync_{channel}"),
        )
        # 홈이 아닐 때 전체 동기화도 보조로 제공
        clicked_all = False
        if page != "home":
            clicked_all = st.button(
                "전체 동기화",
                use_container_width=True,
                key=_wk("toolbar_full_sync_extra"),
                help="네이버·구글·홈페이지를 한 번에 동기화",
            )
    with col_pdf:
        week_off = _current_week_off()
        _, _, pdf_mon, _ = time_utils.calendar_week_bounds(week_off)
        ai_for_pdf = ""
        gem_sess = st.session_state.get("gemini_chat_session")
        if isinstance(gem_sess, dict):
            for msg in gem_sess.get("messages") or []:
                if msg.get("role") == "model" and msg.get("display", True):
                    ai_for_pdf = str(msg.get("content") or "")
                    break
        try:
            pdf_bytes = _cached_pdf_bytes(week_off, ai_for_pdf)
            st.download_button(
                "📄 PDF 보고서",
                data=pdf_bytes,
                file_name=f"weekly_ad_{pdf_mon.isoformat()}.pdf",
                mime="application/pdf",
                use_container_width=True,
                key=_wk("dl_pdf_report"),
            )
            if ai_for_pdf:
                st.caption("③ AI 분석 내용이 PDF에 포함됩니다.")
            else:
                st.caption("③ AI 분석 실행 후 PDF를 받으면 AI 섹션이 포함됩니다.")
        except Exception as exc:  # noqa: BLE001
            st.button("📄 PDF 보고서", disabled=True, use_container_width=True)
            st.caption(f"PDF 오류: {exc}")
        st.caption("가로 · PPT형")

    run_channel: str | None = None
    if clicked:
        run_channel = channel
    elif clicked_all:
        run_channel = "all"

    if run_channel:
        summary = _run_channel_sync(run_channel, weeks=weeks, force=force)
        st.session_state["last_full_sync"] = summary
        _cached_pdf_bytes.clear()

    # 직전 동기화 결과 요약(완료 표시 유지)
    last = st.session_state.get("last_full_sync")
    if isinstance(last, dict) and last:
        ch = str(last.get("channel") or "all")
        ch_label = {
            "all": "전체",
            "naver": "네이버",
            "google": "구글",
            "homepage": "홈페이지",
        }.get(ch, ch)
        hourly = last.get("hourly") or {}
        pts = int(hourly.get("ad_points", 0)) if isinstance(hourly, dict) else 0
        mode = hourly.get("mode", "") if isinstance(hourly, dict) else ""

        parts: list[str] = []
        if isinstance(hourly, dict) and hourly:
            parts.append(f"시간별 {pts:,}건({mode})")
        for key, name in (
            ("weekly", "네이버 주간"),
            ("google_weekly", "구글 주간"),
            ("ga4_weekly", "홈페이지 GA4"),
        ):
            rows = last.get(key) or []
            if not isinstance(rows, list) or not rows:
                continue
            new_ok, cached_n, err_n = _summarize_week_rows(rows)
            if err_n:
                parts.append(f"{name} 성공 {new_ok + cached_n}주 / 경고 {err_n}주")
            else:
                parts.append(f"{name} 신규 {new_ok} · 유지 {cached_n}")

        err_keys = ("hourly_error", "weekly_error", "google_weekly_error", "ga4_weekly_error")
        has_err = any(last.get(k) for k in err_keys) or any(
            isinstance(r, dict) and ((not r.get("ok")) or r.get("error"))
            for key in ("weekly", "google_weekly", "ga4_weekly")
            for r in (last.get(key) or [])
        )
        msg = f"최근 {ch_label} 동기화" + (" — " + " · ".join(parts) if parts else " 완료")
        if has_err:
            st.warning(msg)
        else:
            st.success(msg + " ✅")


def _current_week_off() -> int:
    _, label_to_offset = _dash_week_lookups()
    sel = st.session_state.get("dash_week_pick")
    if sel not in label_to_offset:
        return -1
    return int(label_to_offset[sel])


@st.cache_data(show_spinner="PDF 보고서 생성 중…", ttl=120)
def _cached_pdf_bytes(week_off: int, ai_text: str = "") -> bytes:
    return pdf_report.build_weekly_pdf(week_off, ai_text=ai_text or None)


def _render_keyword_top20_compare(
    curr_mon,
    curr_sun,
    prev_mon,
    prev_sun,
    *,
    key_prefix: str = "kw20",
    show_caption: bool = True,
) -> None:
    """키워드 TOP 20 — 클릭 수 주간 비교."""
    st.markdown("#### 키워드 TOP 20 · 클릭 비교")
    if show_caption:
        st.caption("해당 주 **클릭 수** 기준 상위 20개")
    kw = naver_weekly_report.keyword_clicks_compare(
        curr_mon.isoformat(), prev_mon.isoformat(), top_n=20
    )
    if kw.empty:
        st.info("두 주 모두 키워드 데이터가 있어야 비교할 수 있습니다. 상단 「🔄 전체 동기화」를 실행해 주세요.")
        return
    show = kw.copy()
    show = show.rename(
        columns={
            "rank": "순위",
            "keyword": "키워드",
            "campaign_name": "캠페인",
            "clicks_curr": "클릭(해당 주)",
            "clicks_prev": "클릭(그 전 주)",
            "click_change": "클릭 증감",
            "click_change_pct": "증감률(%)",
        }
    )
    show["증감률(%)"] = show["증감률(%)"].apply(lambda x: f"{x:+.1f}%" if x is not None else "–")
    cols = ["순위", "키워드", "클릭(해당 주)", "클릭(그 전 주)", "클릭 증감", "증감률(%)", "캠페인"]
    out_df = show[[c for c in cols if c in show.columns]]
    st.table(_style_week_compare(out_df))
    st.download_button(
        "TOP 20 CSV 다운로드",
        data=out_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"keyword_top20_{curr_mon.isoformat()}.csv",
        mime="text/csv",
        key=_wk(f"{key_prefix}_dl_kw_top20_{curr_mon.isoformat()}"),
    )


def _render_naver_visual_charts(
    curr_mon,
    curr_sun,
    prev_mon,
    prev_sun,
    *,
    key_prefix: str = "viz",
) -> None:
    """캠페인 비용 원형(주간 비교) + 네이버 지표 막대 — 스케일 분리."""
    p_tot = naver_weekly_report.week_totals(prev_mon.isoformat())
    c_tot = naver_weekly_report.week_totals(curr_mon.isoformat())
    prev_camp = naver_weekly_report.campaigns_df(prev_mon.isoformat())
    curr_camp = naver_weekly_report.campaigns_df(curr_mon.isoformat())

    prev_lbl = f"그 전 주 · {_week_short_label(prev_mon, prev_sun)}"
    curr_lbl = f"해당 주 · {_week_short_label(curr_mon, curr_sun)}"

    pie_pair = charts.campaign_cost_pie_pair_plotly(
        prev_camp,
        curr_camp,
        prev_title=prev_lbl,
        curr_title=curr_lbl,
    )
    if pie_pair is not None:
        st.plotly_chart(
            pie_pair,
            use_container_width=True,
            config={"responsive": True},
            key=_wk(f"{key_prefix}_pie_pair"),
        )
    else:
        st.info("캠페인 비용 데이터가 없습니다. 「전체 동기화」 후 다시 확인하세요.")

    bar = charts.naver_week_compare_bar_plotly(
        p_tot,
        c_tot,
        prev_label=prev_lbl,
        curr_label=curr_lbl,
    )
    st.plotly_chart(
        bar,
        use_container_width=True,
        config={"responsive": True},
        key=_wk(f"{key_prefix}_bar"),
    )


def _week_short_label(mon, sun) -> str:
    return f"{mon.month:02d}/{mon.day:02d}~{sun.month:02d}/{sun.day:02d}"


def _render_campaign_weekly_compare(
    curr_mon,
    curr_sun,
    prev_mon,
    prev_sun,
    *,
    key_prefix: str = "camp",
    show_caption: bool = True,
) -> None:
    """캠페인별 — 해당 주 vs 그 전 주 비용·클릭·CPC."""
    st.markdown("#### 캠페인별 주간 비교 (상세)")
    if show_caption:
        st.caption("해당 주 **비용** 기준 · 활동 캠페인만")
    cmp_df = naver_weekly_report.campaign_weekly_compare(
        curr_mon.isoformat(), prev_mon.isoformat()
    )
    if cmp_df.empty:
        st.info("두 주 캠페인 데이터가 없습니다. 상단 「🔄 전체 동기화」를 실행해 주세요.")
        return
    show = cmp_df.rename(
        columns={
            "rank": "순위",
            "campaign_name": "캠페인",
            "cost_curr": "비용(해당 주)",
            "cost_prev": "비용(그 전 주)",
            "cost_change": "비용 증감",
            "clicks_curr": "클릭(해당 주)",
            "clicks_prev": "클릭(그 전 주)",
            "click_change": "클릭 증감",
            "cpc_curr": "CPC(해당 주)",
            "cpc_prev": "CPC(그 전 주)",
            "impressions_curr": "노출(해당 주)",
            "impressions_prev": "노출(그 전 주)",
        }
    )
    cols = [
        "순위",
        "캠페인",
        "비용(해당 주)",
        "비용(그 전 주)",
        "비용 증감",
        "클릭(해당 주)",
        "클릭(그 전 주)",
        "클릭 증감",
        "CPC(해당 주)",
        "CPC(그 전 주)",
    ]
    out_df = show[[c for c in cols if c in show.columns]]
    st.table(_style_week_compare(out_df))
    st.download_button(
        "캠페인 주간 비교 CSV",
        data=out_df.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"campaign_compare_{curr_mon.isoformat()}.csv",
        mime="text/csv",
        key=_wk(f"{key_prefix}_dl_camp_{curr_mon.isoformat()}"),
    )


def _render_gemini_weekly_analysis() -> None:
    st.markdown("### 🤖 AI 주간 분석")
    _, _, curr_mon, curr_sun = time_utils.calendar_week_bounds(-1)
    _, _, prev_mon, prev_sun = time_utils.calendar_week_bounds(-2)
    st.caption(_week_compare_caption(curr_mon, curr_sun, prev_mon, prev_sun))
    st.caption("GA4·광고·전환과 Rhymix 서버 원본 트래픽을 함께 분석합니다. 분석 후 **📄 PDF 보고서**에도 자동 포함됩니다.")
    if config.RHYMIX_TRAFFIC_ENABLED:
        st.caption("✅ 서버 트래픽 정밀 분석 사용 중 · 집계 데이터만 AI 분석에 포함되며 IP 원문은 전송하지 않습니다.")
    else:
        st.caption("ℹ️ 서버 트래픽 정밀 분석은 비활성 상태입니다. `.env`에서 명시적으로 활성화할 수 있습니다.")
    if not (config.GEMINI_API_KEY or "").strip():
        with st.container(border=True):
            st.info("`.env` 파일에 `GEMINI_API_KEY`를 설정하면 AI 주간 분석을 사용할 수 있습니다.")
        return

    with st.container(border=True):
        btn_col1, btn_col2 = st.columns([2, 1])
        with btn_col1:
            start_clicked = st.button(
                "총체적 주간 분석 시작",
                type="primary",
                key=_wk("gemini_analyze"),
                use_container_width=True,
            )
        with btn_col2:
            if st.button("대화 초기화", key=_wk("gemini_reset"), use_container_width=True):
                st.session_state.pop("gemini_chat_session", None)
                _cached_pdf_bytes.clear()
                st.rerun()

        if start_clicked:
            with st.spinner("전체 데이터를 모아 Gemini가 분석 중…"):
                session, err = gemini_weekly_analysis.start_weekly_chat()
            if err:
                st.error(err)
            elif session:
                st.session_state["gemini_chat_session"] = session
                _cached_pdf_bytes.clear()
                st.rerun()

        session = st.session_state.get("gemini_chat_session")
        if not session:
            st.info("「총체적 주간 분석 시작」을 누르면 보고서가 생성됩니다. 이후 아래에서 추가 질문을 이어갈 수 있습니다.")
            return

        period = session.get("report_period", "")
        if period:
            st.caption(f"분석 기준 기간: **{period}** · 아래 대화에서 이어서 질문하세요.")

        for msg in session.get("messages") or []:
            if not msg.get("display", True):
                continue
            role = msg.get("role", "model")
            with st.chat_message("assistant" if role == "model" else "user"):
                st.markdown(str(msg.get("content") or ""))

        user_followup = st.chat_input(
            "추가 질문 (예: CPC가 오른 캠페인만 정리해줘 / 키워드 ○○ 어떻게 대응할까?)",
            key=_wk("gemini_chat_input"),
        )
        if user_followup:
            with st.spinner("답변 생성 중…"):
                updated, err = gemini_weekly_analysis.continue_chat(session, user_followup)
            if err:
                st.error(err)
            elif updated:
                st.session_state["gemini_chat_session"] = updated
                _cached_pdf_bytes.clear()
                st.rerun()

    st.markdown("---")


def render_dashboard_sidebar_only() -> None:
    """접었다 펼 수 있는 사이드바 — 메뉴·조회 주·섹션 필터."""
    db.init_db()
    week_labels, _ = _dash_week_lookups()
    with st.sidebar:
        st.markdown("##### 메뉴")
        st.radio(
            "메뉴",
            options=_PAGE_LABELS,
            index=0,
            key="dash_page_pick",
            label_visibility="collapsed",
        )
        st.markdown("##### 조회 주")
        st.selectbox(
            "조회 주 (월~일)",
            week_labels,
            index=0,
            key="dash_week_pick",
            label_visibility="collapsed",
        )
        with st.expander("표시할 섹션", expanded=False):
            pick = st.multiselect(
                "섹션",
                options=_SECTION_LABELS,
                default=_SECTION_LABELS,
                key="dash_section_pick",
                label_visibility="collapsed",
            )
            if not pick:
                st.warning("한 개 이상 선택하세요.")
        st.caption("왼쪽 메뉴는 접어도 됩니다. 주요 정보는 메인 화면에 있습니다.")


def _merged_for_week_off(merged_all: pd.DataFrame, week_off: int) -> pd.DataFrame:
    w_start, w_end, _, _ = time_utils.calendar_week_bounds(week_off)
    if merged_all.empty:
        return merged_all.iloc[0:0].copy()
    return merged_all[(merged_all["ts_hour"] >= w_start) & (merged_all["ts_hour"] <= w_end)].copy()


def _pct_delta(cur: float, prev: float) -> float | None:
    if prev <= 0 and cur <= 0:
        return None
    if prev <= 0:
        return None
    return (cur - prev) / prev * 100.0


def _week_compare_caption(this_mon, this_sun, prev_mon, prev_sun) -> str:
    """해당 주·그 전 주 — 날짜를 작은 글씨로 표시."""
    this_rng = time_utils.format_week_range(this_mon, this_sun)
    prev_rng = time_utils.format_week_range(prev_mon, prev_sun)
    return f"**해당 주** · {this_rng}  ·  **그 전 주** · {prev_rng}"


def _style_week_compare(df: pd.DataFrame):
    """
    표 안에서 "해당 주"/"그 전 주" 컬럼을 배너와 같은 팔레트로 옅게 구분.
    컬럼명에 해당 문자열이 들어있으면 자동 적용 — 호출부는 그대로 두고 표시 직전에만 감싸면 됨.
    """
    curr_cols = [c for c in df.columns if "해당 주" in str(c)]
    prev_cols = [c for c in df.columns if "그 전 주" in str(c)]
    styler = df.style
    if curr_cols:
        styler = styler.set_properties(subset=curr_cols, **{"background-color": "rgba(26,115,232,0.10)"})
    if prev_cols:
        styler = styler.set_properties(subset=prev_cols, **{"background-color": "rgba(217,48,37,0.08)"})
    return styler


def _render_week_period_banner(
    *,
    this_mon,
    this_sun,
    prev_mon,
    prev_sun,
    heading: str = "주간 비교 구간",
    note: str | None = None,
) -> None:
    """해당 주 vs 그 전 주 — 라벨 + 날짜(작은 글씨)."""
    this_range = time_utils.format_week_range(this_mon, this_sun)
    prev_range = time_utils.format_week_range(prev_mon, prev_sun)
    with st.container(border=True):
        st.markdown(f"## 📅 {heading}")
        col_l, col_mid, col_r = st.columns([5, 1, 5])
        with col_l:
            st.markdown(
                f"""
<div style="padding:14px 16px;border-radius:10px;background:#e8f0fe;border-left:5px solid #1a73e8;">
  <div style="font-size:17px;font-weight:700;color:#111;margin-bottom:6px;">해당 주</div>
  <div style="font-size:12px;color:#555;line-height:1.45;">{this_range}</div>
</div>
                """,
                unsafe_allow_html=True,
            )
        with col_mid:
            st.markdown(
                '<div style="text-align:center;padding-top:22px;font-size:20px;font-weight:800;color:#888;">VS</div>',
                unsafe_allow_html=True,
            )
        with col_r:
            st.markdown(
                f"""
<div style="padding:14px 16px;border-radius:10px;background:#fce8e6;border-left:5px solid #d93025;">
  <div style="font-size:17px;font-weight:700;color:#111;margin-bottom:6px;">그 전 주</div>
  <div style="font-size:12px;color:#555;line-height:1.45;">{prev_range}</div>
</div>
                """,
                unsafe_allow_html=True,
            )
        st.caption(_week_compare_caption(this_mon, this_sun, prev_mon, prev_sun))
        if note:
            st.caption(note)


def _render_weekly_report_hero(merged_all: pd.DataFrame) -> None:
    """
    사이드바에서 고른 주(week offset, 기본 -1 = 지난 완료 월~일) vs 그 전 주.
    지표 우선순위: 클릭 → CTR·CPC → 비용 → 전환(ROAS).
    """
    week_off = _current_week_off()
    _, _, last_mon, last_sun = time_utils.calendar_week_bounds(week_off)
    _, _, prev_mon, prev_sun = time_utils.calendar_week_bounds(week_off - 1)

    m_last = _merged_for_week_off(merged_all, week_off)
    m_prev = _merged_for_week_off(merged_all, week_off - 1)

    wa_last = merge.week_ad_performance(m_last, week_start=last_mon.isoformat())
    wa_prev = merge.week_ad_performance(m_prev, week_start=prev_mon.isoformat())
    tot_last = merge.period_totals(m_last)
    tot_prev = merge.period_totals(m_prev)

    clk_l, clk_p = wa_last["total_clicks"], wa_prev["total_clicks"]
    cost_l, cost_p = wa_last["total_cost"], wa_prev["total_cost"]
    ctr_l, ctr_p = wa_last["blended_ctr_pct"], wa_prev["blended_ctr_pct"]
    imp_l, imp_p = wa_last["total_impressions"], wa_prev["total_impressions"]
    roas_l, roas_p = float(tot_last["roas"]), float(tot_prev["roas"])

    d_clk = _pct_delta(clk_l, clk_p)
    d_cost = _pct_delta(cost_l, cost_p)
    d_roas = _pct_delta(roas_l, roas_p) if roas_p > 0 else None
    d_ctr = _pct_delta(ctr_l, ctr_p) if imp_l >= 500 and imp_p >= 500 else None

    sent_parts: list[str] = []

    def _sentence(label: str, d: float | None) -> None:
        if d is None:
            return
        if abs(d) < 0.05:
            sent_parts.append(f"{label}은(는) 그 전 주와 거의 같은 수준입니다.")
            return
        if d > 0:
            sent_parts.append(f"{label}은(는) 그 전 주보다 약 {abs(d):.1f}% 높았습니다.")
        else:
            sent_parts.append(f"{label}은(는) 그 전 주보다 약 {abs(d):.1f}% 낮았습니다.")

    _sentence("통합 광고 클릭 수", d_clk)
    _sentence("노출 대비 CTR(주간 블렌드)", d_ctr)
    _sentence("통합 광고 비용", d_cost)
    _sentence("ROAS(전환가치÷비용)", d_roas)

    if ctr_l != ctr_p and d_ctr is None and imp_l >= 50 and imp_p >= 50:
        pp = ctr_l - ctr_p
        sent_parts.append(
            f"CTR은 지난 주 {ctr_l:.2f}% 그 전 주 {ctr_p:.2f}%로, 약 {pp:+.2f}%p 차이였습니다. "
            "(노출 규모가 작으면 증감률 멘트는 생략합니다.)"
        )

    if m_last.empty and m_prev.empty:
        briefing = (
            "직전 두 주 데이터가 아직 없습니다. "
            "오른쪽 상단 「지금 동기화」와 「네이버 주간 수집」을 실행한 뒤 새로고침하세요."
        )
    elif not sent_parts:
        briefing = (
            "그 전 주 대비 변화가 매우 작거나 비교 분모가 0이어요. "
            "아래 숫자(클릭·CTR·CPC)를 직접 비교하는 것을 권장합니다."
        )
    else:
        briefing = "주간 브리핑 — " + " ".join(sent_parts[:6])

    st.caption("아래 숫자는 **해당 주** 기준이며, 증감률(%)은 **그 전 주** 대비입니다.")

    with st.container(border=True):
        st.markdown("### 주간 브리핑 — 통합 광고")
        st.markdown(briefing)

    def _delta_label(dp: float | None) -> str | None:
        if dp is None:
            return None
        return f"{dp:+.1f}% (그 전 주 대비)"

    def _delta_pp(cur: float, prev: float) -> str | None:
        if abs(cur - prev) < 1e-9:
            return "0.00%p"
        return f"{cur - prev:+.2f}%p"

    h1, h2, h3, h4, h5 = st.columns(5)
    h1.metric(
        "통합 클릭 (해당 주)",
        f"{int(max(0, clk_l)):,}",
        delta=_delta_label(d_clk),
    )
    h2.metric(
        "블렌드 CTR (해당 주)",
        f"{ctr_l:.2f}%",
        delta=_delta_pp(ctr_l, ctr_p),
    )
    h3.metric(
        "블렌드 CPC (해당 주)",
        f"{wa_last['blended_cpc']:,.0f} 원",
        delta=None,
    )
    h4.metric(
        "통합 광고비 (해당 주)",
        f"{cost_l:,.0f} 원",
        delta=_delta_label(d_cost),
        delta_color="inverse",
    )
    h5.metric(
        "ROAS (해당 주)",
        f"{roas_l:.2f}x" if roas_l else "–",
        delta=_delta_label(d_roas),
    )

    nc_l = wa_last["naver_clicks"]
    gc_l = wa_last["google_clicks"]

    st.caption(
        f"채널 클릭 · 네이버 **{nc_l:,.0f}** / 구글 **{gc_l:,.0f}** · "
        f"주간 노출 **{imp_l:,.0f}**"
    )
    st.markdown("---")


def _render_naver_weekly_comparison_report() -> None:
    """네이버 API 캠페인 합계 기준 — 사이드바에서 고른 주 vs 그 전 주(완료 월~일)."""
    week_off = _current_week_off()
    _, _, curr_mon, curr_sun = time_utils.calendar_week_bounds(week_off)
    _, _, prev_mon, prev_sun = time_utils.calendar_week_bounds(week_off - 1)
    p_tot = naver_weekly_report.week_totals(prev_mon.isoformat())
    c_tot = naver_weekly_report.week_totals(curr_mon.isoformat())

    st.markdown(
        f"### 📊 네이버 캠페인 [실제 총계] 기준 주간 성과 비교"
    )

    missing = []
    if not p_tot.get("has_snapshot"):
        missing.append(f"그 전 주 ({prev_mon})")
    if not c_tot.get("has_snapshot"):
        missing.append(f"해당 주 ({curr_mon})")
    if missing:
        st.warning("스냅샷 없음: " + ", ".join(missing) + " — 아래 재조회 버튼을 눌러주세요.")

    need_cost_refresh = (p_tot.get("has_snapshot") and float(p_tot.get("cost") or 0) <= 0) or (
        c_tot.get("has_snapshot") and float(c_tot.get("cost") or 0) <= 0
    )
    if need_cost_refresh:
        st.warning(
            "비용(salesAmt)이 0으로 저장된 주차가 있습니다. 예전 수집본일 수 있어 **두 주 재조회**가 필요합니다."
        )

    c1, c2 = st.columns(2)
    with c1:
        if st.button(
            f"해당 주·그 전 주 재조회 · {_fmt_naver_short(prev_mon)}~{_fmt_naver_short(curr_sun)}",
            key=_wk("naver_report_requery_pair"),
        ):
            with st.spinner("두 주차 네이버 스냅샷 재조회 중…"):
                r1 = naver_weekly_views.sync_naver_week_one(prev_mon.isoformat())
                r2 = naver_weekly_views.sync_naver_week_one(curr_mon.isoformat())
            st.session_state["naver_report_pair_sync"] = [r1, r2]
            st.rerun()
    with c2:
        pair_hist = st.session_state.get("naver_report_pair_sync")
        if isinstance(pair_hist, list) and pair_hist:
            ok_n = sum(1 for r in pair_hist if bool(r.get("ok")))
            if ok_n == len(pair_hist):
                st.success(f"직전 재조회: {ok_n}주 성공")
            else:
                st.warning(f"직전 재조회: {ok_n}/{len(pair_hist)}주 성공")

    summary = naver_weekly_report.build_summary_table(prev_mon, prev_sun, curr_mon, curr_sun)
    st.table(_style_week_compare(summary))

    st.markdown("#### 📈 한눈에 보기")
    _render_naver_visual_charts(curr_mon, curr_sun, prev_mon, prev_sun, key_prefix="naver_report")

    st.markdown("#### 🔍 캠페인별 독주·예산 구조 분석")
    try:
        analysis_lines = naver_weekly_report.build_campaign_analysis(
            prev_mon, prev_sun, curr_mon, curr_sun
        )
        for line in analysis_lines:
            st.markdown(f"- {line}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"캠페인 분석 생성 중 오류: {exc}")

    _render_campaign_weekly_compare(
        curr_mon, curr_sun, prev_mon, prev_sun, key_prefix="naver_report", show_caption=False
    )

    _render_keyword_top20_compare(
        curr_mon, curr_sun, prev_mon, prev_sun, key_prefix="naver_report", show_caption=False
    )
    st.markdown("---")



def _render_google_weekly_comparison_report() -> None:
    """구글 주간 성과 — 선택 주 vs 그 전 주."""
    st.markdown("### 🔵 구글 광고 · 주간 성과 비교")
    week_off = _current_week_off()
    _, _, curr_mon, curr_sun = time_utils.calendar_week_bounds(week_off)
    _, _, prev_mon, prev_sun = time_utils.calendar_week_bounds(week_off - 1)
    p_tot = google_weekly_report.week_totals(prev_mon.isoformat())
    c_tot = google_weekly_report.week_totals(curr_mon.isoformat())

    if week_off == 0:
        st.caption("스냅샷이 없으면 상단 「구글 동기화」를 실행하세요.")

    missing = []
    if not p_tot.get("has_snapshot"):
        missing.append(f"그 전 주 ({prev_mon})")
    if not c_tot.get("has_snapshot"):
        missing.append(f"해당 주 ({curr_mon})")
    if missing:
        st.warning("스냅샷 없음: " + ", ".join(missing))

    with st.expander("복구 · API 강제 재수집 (필요 시에만)", expanded=bool(missing)):
        if st.button(
            f"해당 주·그 전 주 구글 재조회 · {_fmt_naver_short(prev_mon)}~{_fmt_naver_short(curr_sun)}",
            key=_wk("google_report_requery_pair"),
        ):
            with st.spinner("두 주차 구글 재조회 중…"):
                r1 = google_weekly_views.sync_google_week_one(prev_mon.isoformat())
                r2 = google_weekly_views.sync_google_week_one(curr_mon.isoformat())
            st.session_state["google_report_pair_sync"] = [r1, r2]
            st.rerun()

    if not (p_tot.get("has_snapshot") or c_tot.get("has_snapshot")):
        st.info("아직 구글 주간 데이터가 없습니다. 「구글 동기화」를 실행해 주세요.")
        return

    m1, m2, m3, m4 = st.columns(4)
    m1.metric(
        "비용 (해당 주)", f"{float(c_tot.get('cost') or 0):,.0f} 원",
        delta=f"{float(c_tot.get('cost') or 0) - float(p_tot.get('cost') or 0):+,.0f} 원 (그 전 주 대비)",
    )
    m2.metric(
        "클릭 (해당 주)", f"{int(c_tot.get('clicks') or 0):,}",
        delta=f"{int(c_tot.get('clicks') or 0) - int(p_tot.get('clicks') or 0):+,} (그 전 주 대비)",
    )
    m3.metric(
        "노출 (해당 주)", f"{int(c_tot.get('impressions') or 0):,}",
        delta=f"{int(c_tot.get('impressions') or 0) - int(p_tot.get('impressions') or 0):+,} (그 전 주 대비)",
    )
    m4.metric(
        "전환 (해당 주)", f"{float(c_tot.get('conversions') or 0):,.1f}",
        delta=f"{float(c_tot.get('conversions') or 0) - float(p_tot.get('conversions') or 0):+,.1f} (그 전 주 대비)",
    )
    st.caption(
        f"그 전 주 — 비용 {float(p_tot.get('cost') or 0):,.0f}원 · 클릭 {int(p_tot.get('clicks') or 0):,} · "
        f"노출 {int(p_tot.get('impressions') or 0):,} · 전환 {float(p_tot.get('conversions') or 0):,.1f}"
    )

    st.markdown("#### 캠페인별 주간 비교 (상세)")
    cmp_df = google_weekly_report.campaign_weekly_compare(curr_mon.isoformat(), prev_mon.isoformat())
    if cmp_df.empty:
        st.info("두 주 캠페인 데이터가 없습니다. 상단 「🔄 전체 동기화」를 실행해 주세요.")
    else:
        show = cmp_df.rename(
            columns={
                "rank": "순위",
                "campaign_name": "캠페인",
                "cost_curr": "비용(해당 주)",
                "cost_prev": "비용(그 전 주)",
                "cost_change": "비용 증감",
                "clicks_curr": "클릭(해당 주)",
                "clicks_prev": "클릭(그 전 주)",
                "click_change": "클릭 증감",
                "cpc_curr": "CPC(해당 주)",
                "cpc_prev": "CPC(그 전 주)",
                "impressions_curr": "노출(해당 주)",
                "impressions_prev": "노출(그 전 주)",
            }
        )
        cols = [
            "순위",
            "캠페인",
            "비용(해당 주)",
            "비용(그 전 주)",
            "비용 증감",
            "클릭(해당 주)",
            "클릭(그 전 주)",
            "클릭 증감",
            "CPC(해당 주)",
            "CPC(그 전 주)",
        ]
        out_df = show[[c for c in cols if c in show.columns]]
        st.table(_style_week_compare(out_df))
        st.download_button(
            "구글 캠페인 주간 비교 CSV",
            data=out_df.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"google_campaign_compare_{curr_mon.isoformat()}.csv",
            mime="text/csv",
            key=_wk(f"google_report_dl_camp_{curr_mon.isoformat()}"),
        )
    _render_google_ads_control_center()
    st.markdown("---")


def _render_google_ads_control_center() -> None:
    """Read-only diagnosis plus proposal -> approval -> apply workflow."""
    st.markdown("#### 🛡️ 구글 광고 효율 관리 · 승인 후 실행")
    st.caption(
        "먼저 원인을 조사하고 변경안을 만듭니다. 승인 전에는 광고 계정을 바꾸지 않으며, "
        "승인 후에도 ‘실행’ 버튼을 한 번 더 눌러야 실제 반영됩니다."
    )

    if st.button("최신 동일 요일 감소 원인 조사", key=_wk("google_live_diagnose")):
        today = date.today()
        this_mon = today - timedelta(days=today.weekday())
        prev_mon = this_mon - timedelta(days=7)
        prev_end = prev_mon + timedelta(days=today.weekday())
        with st.spinner("구글 광고의 최신 실적과 캠페인 상태를 읽는 중입니다..."):
            try:
                current = google_weekly_views.fetch_campaign_week(this_mon, today)
                previous = google_weekly_views.fetch_campaign_week(prev_mon, prev_end)
                settings = google_ads_actions.fetch_live_campaign_settings()
                st.session_state["google_live_diagnosis"] = {
                    "current": current,
                    "previous": previous,
                    "settings": settings,
                    "current_label": f"{this_mon} ~ {today}",
                    "previous_label": f"{prev_mon} ~ {prev_end}",
                }
            except Exception as exc:  # noqa: BLE001
                st.error(f"최신 광고 상태를 읽지 못했습니다: {exc}")

    diag = st.session_state.get("google_live_diagnosis")
    if isinstance(diag, dict):
        current = list(diag.get("current") or [])
        previous = list(diag.get("previous") or [])
        settings = list(diag.get("settings") or [])
        curr_tot = {
            "cost": sum(float(r.get("cost") or 0) for r in current),
            "clicks": sum(int(r.get("clicks") or 0) for r in current),
            "impressions": sum(int(r.get("impressions") or 0) for r in current),
            "conversions": sum(float(r.get("conversions") or 0) for r in current),
        }
        prev_tot = {
            "cost": sum(float(r.get("cost") or 0) for r in previous),
            "clicks": sum(int(r.get("clicks") or 0) for r in previous),
            "impressions": sum(int(r.get("impressions") or 0) for r in previous),
            "conversions": sum(float(r.get("conversions") or 0) for r in previous),
        }
        st.info(f"같은 요일끼리 비교: {diag.get('current_label')} vs {diag.get('previous_label')}")
        cols = st.columns(4)
        for col, label, key, fmt in (
            (cols[0], "비용", "cost", ",.0f"),
            (cols[1], "클릭", "clicks", ",.0f"),
            (cols[2], "노출", "impressions", ",.0f"),
            (cols[3], "전환", "conversions", ",.1f"),
        ):
            before = float(prev_tot[key])
            now = float(curr_tot[key])
            pct = 100.0 * (now - before) / before if before else 0.0
            col.metric(label, format(now, fmt), delta=f"{pct:+.1f}%")

        limited = [r for r in settings if r.get("primary_status") == "LIMITED"]
        policy_limited = [
            r for r in limited if any("POLICY" in str(reason) for reason in r.get("primary_status_reasons", []))
        ]
        if policy_limited:
            st.error(
                "정책 제한이 걸린 캠페인이 있습니다. 예산을 늘리기 전에 Google Ads의 ‘정책 관리자’에서 "
                "제한된 광고·이미지·문구를 수정하거나 이의신청해야 합니다."
            )
            st.dataframe(
                pd.DataFrame(policy_limited)[
                    ["campaign_name", "primary_status", "primary_status_reasons", "daily_budget_won"]
                ].rename(
                    columns={
                        "campaign_name": "캠페인",
                        "primary_status": "현재 상태",
                        "primary_status_reasons": "제한 이유",
                        "daily_budget_won": "현재 일예산(원)",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

    st.markdown("##### 목표 광고비 설정")
    st.caption("월 목표액을 일예산으로 나누어 각 활성 캠페인에 같은 비율로 조정하는 변경안을 만듭니다.")
    monthly_target = st.number_input(
        "구글 광고 월 목표 금액(원)", min_value=100_000, max_value=100_000_000,
        value=2_000_000, step=100_000, key=_wk("google_monthly_budget_target"),
    )
    target_daily_total = float(monthly_target) / 30.4
    st.caption(f"월 {monthly_target:,.0f}원은 전체 캠페인 일예산 합계 약 {target_daily_total:,.0f}원에 해당합니다.")

    if st.button("목표 예산 변경안 만들기", key=_wk("google_budget_plan")):
        with st.spinner("현재 일예산을 확인하고 안전한 변경안을 만드는 중입니다..."):
            try:
                settings = google_ads_actions.fetch_live_campaign_settings()
                enabled = [r for r in settings if r.get("status") == "ENABLED" and r.get("daily_budget_won", 0) > 0]
                current_daily = sum(float(r["daily_budget_won"]) for r in enabled)
                raw_pct = 100.0 * (target_daily_total - current_daily) / current_daily if current_daily else 0.0
                safe_pct = max(-abs(config.GOOGLE_ADS_MAX_BUDGET_CHANGE_PCT), min(abs(config.GOOGLE_ADS_MAX_BUDGET_CHANGE_PCT), raw_pct))
                proposals = []
                for row in enabled[: int(config.GOOGLE_AI_PROPOSAL_MAX_PER_RUN)]:
                    old = float(row["daily_budget_won"])
                    new = max(1_000.0, round(old * (1.0 + safe_pct / 100.0), -2))
                    proposals.append(
                        {
                            "action_type": "campaign_budget_change",
                            "target_type": "campaign",
                            "campaign_name": row["campaign_name"],
                            "current_value": f"{old:,.0f}원",
                            "proposed_value": f"{new:,.0f}원",
                            "change_pct": safe_pct,
                            "rationale": f"월 목표 {monthly_target:,.0f}원에 맞춘 1차 조정입니다. 한 번의 변경은 안전상 ±{config.GOOGLE_ADS_MAX_BUDGET_CHANGE_PCT:.0f}% 이내로 제한합니다.",
                            "priority": "medium",
                            "confidence": "high",
                        }
                    )
                ids = google_ads_actions.resolve_and_store_proposals(
                    proposals, f"월 목표 광고비 {monthly_target:,.0f}원", "month",
                    date.today().replace(day=1).isoformat(), f"월 목표 {monthly_target:,.0f}원",
                )
                st.success(f"실행 전 검토용 변경안 {len(ids)}개를 만들었습니다. 아래에서 각각 승인해 주세요.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"목표 예산 변경안을 만들지 못했습니다: {exc}")

    if st.button("AI 효율 개선안 만들기", key=_wk("google_ai_proposals")):
        with st.spinner("성과 데이터를 분석해 실행 전 검토안을 만드는 중입니다..."):
            proposals, summary, err = gemini_weekly_analysis.propose_google_ads_actions("week")
            if err:
                st.error(err)
            else:
                ids = google_ads_actions.resolve_and_store_proposals(
                    proposals, summary, "week", time_utils.calendar_week_bounds(-1)[2].isoformat(), "최근 완료 주",
                )
                st.session_state["google_ai_strategy_summary"] = summary
                st.success(f"AI 변경안 {len(ids)}개를 만들었습니다. 아직 광고에는 반영되지 않았습니다.")
    if st.session_state.get("google_ai_strategy_summary"):
        st.info(st.session_state["google_ai_strategy_summary"])

    proposals = [dict(r) for r in db.fetch_google_ai_proposals() if r["status"] in ("pending", "approved")][:20]
    if proposals:
        st.markdown("##### 승인 대기·승인 완료 변경안")
    for row in proposals:
        label = f"#{row['id']} · {row['campaign_name'] or row['keyword_text']} · {row['status']}"
        with st.expander(label, expanded=row["status"] == "approved"):
            st.write(f"**변경:** {row['current_value'] or '-'} → {row['proposed_value'] or '-'} ({float(row['change_pct'] or 0):+.1f}%)")
            st.write(f"**이유:** {row['rationale'] or '-'}")
            st.caption(f"대상 확인: {row['resolution_status']} · {row['resolution_note'] or '정상'}")
            a, b, c = st.columns(3)
            if row["status"] == "pending":
                if a.button("승인", key=_wk(f"approve_google_{row['id']}")):
                    db.update_google_ai_proposal(row["id"], status="approved", reviewed_at=datetime.utcnow().isoformat() + "Z")
                    st.rerun()
                if b.button("거절", key=_wk(f"reject_google_{row['id']}")):
                    db.update_google_ai_proposal(row["id"], status="rejected", reviewed_at=datetime.utcnow().isoformat() + "Z")
                    st.rerun()
            elif row["status"] == "approved":
                if not config.GOOGLE_ADS_ALLOW_WRITE:
                    st.warning("실제 실행 잠금이 켜져 있습니다. Secrets에서 GOOGLE_ADS_ALLOW_WRITE=true로 설정해야 실행할 수 있습니다.")
                if c.button("승인한 변경 실행", key=_wk(f"apply_google_{row['id']}"), disabled=not config.GOOGLE_ADS_ALLOW_WRITE):
                    result = google_ads_actions.apply_proposal(int(row["id"]))
                    if result.get("ok"):
                        st.success("구글 광고 계정에 변경을 적용했습니다.")
                    else:
                        st.error(result.get("error") or "실행 실패")
                    st.rerun()


def _render_ga4_traffic_report() -> None:
    """홈페이지 · GA4 — 선택 주 vs 그 전 주 (채널별 세션 + 주요 전환 이벤트)."""
    import importlib

    importlib.reload(ga4_weekly_views)
    importlib.reload(ga4_weekly_report)

    st.markdown("### 🏠 홈페이지 · GA4 (어디서·어떻게 들어왔는지)")
    st.caption("GA4 세션 기본 채널 그룹(유료 검색/자연 검색/직접 접속 등) + GTM 커스텀 전환 이벤트 기준.")

    if not config.GA4_PROPERTY_ID:
        st.warning(
            "GA4 연동이 아직 설정되지 않았습니다. `.env`에 `GA4_PROPERTY_ID`와 "
            "`GOOGLE_APPLICATION_CREDENTIALS`(GA4 속성에 뷰어 권한이 있는 서비스 계정 JSON 경로)를 설정하세요."
        )
        return

    week_off = _current_week_off()
    _, _, curr_mon, curr_sun = time_utils.calendar_week_bounds(week_off)
    _, _, prev_mon, prev_sun = time_utils.calendar_week_bounds(week_off - 1)
    p_tot = ga4_weekly_report.week_totals(prev_mon.isoformat())
    c_tot = ga4_weekly_report.week_totals(curr_mon.isoformat())

    if week_off == 0:
        st.caption("이번 주를 선택하면 진행 중 주차(월~오늘) 집계가 포함됩니다.")

    missing = []
    if not p_tot.get("has_snapshot"):
        missing.append(f"그 전 주 ({prev_mon})")
    if not c_tot.get("has_snapshot"):
        missing.append(f"해당 주 ({curr_mon})")
    if missing:
        st.warning("스냅샷 없음: " + ", ".join(missing) + " — 아래 재조회 버튼을 눌러주세요.")

    with st.expander("복구 · API 강제 재수집 (필요 시에만)", expanded=bool(missing)):
        if st.button(
            f"해당 주·그 전 주 GA4 재조회 · {_fmt_naver_short(prev_mon)}~{_fmt_naver_short(curr_sun)}",
            key=_wk("ga4_report_requery_pair"),
        ):
            with st.spinner("두 주차 GA4 재조회 중…"):
                r1 = ga4_weekly_views.sync_ga4_week_one(prev_mon.isoformat())
                r2 = ga4_weekly_views.sync_ga4_week_one(curr_mon.isoformat())
            st.session_state["ga4_report_pair_sync"] = [r1, r2]
            st.rerun()
        pair_hist = st.session_state.get("ga4_report_pair_sync")
        if isinstance(pair_hist, list) and pair_hist:
            ok_n = sum(1 for r in pair_hist if bool(r.get("ok")))
            if ok_n == len(pair_hist):
                st.success(f"직전 재조회: {ok_n}주 성공")
            else:
                errs = "; ".join(str(r.get("error")) for r in pair_hist if r.get("error"))
                st.warning(f"직전 재조회: {ok_n}/{len(pair_hist)}주 성공 — {errs}")

    max_backfill = max(4, min(52, int(config.SYNC_LOOKBACK_DAYS) // 7))
    with st.expander("과거 주차 일괄 수집", expanded=False):
        st.caption(
            f"GA4에 실제로 데이터가 있는 주만 채워집니다. "
            f"태그·속성 설치 이전 주는 0으로 나올 수 있습니다. (최대 {max_backfill}주)"
        )
        bf_weeks = st.slider(
            "수집할 주 수 (이번 주 포함)",
            min_value=1,
            max_value=max_backfill,
            value=min(max_backfill, max(1, int(config.GA4_WEEKLY_BACK_WEEKS))),
            key=_wk("ga4_backfill_weeks"),
        )
        bf_force = st.checkbox("이미 DB에 있는 주도 API로 다시 받기", key=_wk("ga4_backfill_force"))
        if st.button("과거 주차 GA4 수집 시작", key=_wk("ga4_backfill_run")):
            with st.spinner(f"최근 {bf_weeks}주 GA4 수집 중…"):
                prog = st.progress(0.0, text="준비 중…")

                def _bf_progress(step: int, total: int, label: str) -> None:
                    prog.progress(min(1.0, step / max(1, total)), text=f"{step}/{total} · {label}")

                rows = ga4_weekly_views.sync_ga4_weekly_back(
                    bf_weeks,
                    skip_existing=not bf_force,
                    include_current=True,
                    progress=_bf_progress,
                )
            st.session_state["ga4_backfill_result"] = rows
            st.rerun()
        bf_res = st.session_state.get("ga4_backfill_result")
        if isinstance(bf_res, list) and bf_res:
            new_ok, cached_n, err_n = _summarize_week_rows(bf_res)
            st.info(f"수집 결과 — 신규 {new_ok}주 · DB 유지 {cached_n}주 · 실패 {err_n}주")

    if not (p_tot.get("has_snapshot") or c_tot.get("has_snapshot")):
        st.info(
            "아직 홈페이지 GA4 주간 데이터가 없습니다. 「홈페이지 동기화」를 실행하거나 "
            "위 재조회 버튼으로 수집하세요."
        )
        return

    daily_compare_key = curr_mon.isoformat()
    if st.session_state.get("ga4_daily_compare_week") != daily_compare_key:
        try:
            st.session_state["ga4_daily_prev"] = fetch_ga4_daily_traffic(prev_mon, prev_sun)
            st.session_state["ga4_daily_curr"] = fetch_ga4_daily_traffic(curr_mon, curr_sun)
            st.session_state["ga4_daily_compare_week"] = daily_compare_key
        except Exception:  # noqa: BLE001
            st.session_state["ga4_daily_prev"] = []
            st.session_state["ga4_daily_curr"] = []

    daily_prev = st.session_state.get("ga4_daily_prev", [])
    daily_curr = st.session_state.get("ga4_daily_curr", [])
    avg_views_curr = (
        sum(int(r.get("page_views", 0)) for r in daily_curr) / len(daily_curr)
        if daily_curr else 0.0
    )
    avg_views_prev = (
        sum(int(r.get("page_views", 0)) for r in daily_prev) / len(daily_prev)
        if daily_prev else 0.0
    )
    sessions_curr = int(c_tot.get("sessions") or 0)
    sessions_prev = int(p_tot.get("sessions") or 0)
    users_curr = int(c_tot.get("active_users") or 0)
    users_prev = int(p_tot.get("active_users") or 0)

    def _week_delta(curr: int, prev: int) -> str | None:
        if prev <= 0:
            return None
        diff = curr - prev
        pct = 100.0 * diff / prev
        return f"{diff:+,} ({pct:+.1f}%)"

    st.caption("해당 주 숫자 아래의 화살표는 그 전 주보다 얼마나 늘거나 줄었는지를 뜻합니다.")
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric(
        "세션 · 해당 주",
        f"{sessions_curr:,}",
        delta=_week_delta(sessions_curr, sessions_prev),
        help="사이트에 들어와 활동한 방문 횟수입니다.",
    )
    m2.metric(
        "활성 사용자 · 해당 주",
        f"{users_curr:,}",
        delta=_week_delta(users_curr, users_prev),
        help="이 기간에 사이트를 이용한 사람 수입니다.",
    )
    m3.metric("세션 · 그 전 주", f"{sessions_prev:,}", help="비교 기준이 되는 지난주의 방문 횟수입니다.")
    m4.metric(
        "활성 사용자 · 그 전 주",
        f"{users_prev:,}",
        help="비교 기준이 되는 지난주의 이용자 수입니다.",
    )
    avg_diff = avg_views_curr - avg_views_prev
    avg_pct = (100.0 * avg_diff / avg_views_prev) if avg_views_prev > 0 else None
    m5.metric(
        "하루 평균 페이지 조회수",
        f"{avg_views_curr:,.1f}회",
        delta=(f"{avg_diff:+,.1f}회 ({avg_pct:+.1f}%)" if avg_pct is not None else None),
        help="해당 주의 전체 페이지 조회수를 수집된 날짜 수로 나눈 값입니다.",
    )
    st.markdown("#### 📅 요일별 방문자 비교")
    st.caption("각 요일에 몇 명이 홈페이지에 들어왔는지 해당 주와 그 전 주를 나란히 비교합니다.")
    daily_compare_key = curr_mon.isoformat()
    if st.session_state.get("ga4_daily_compare_week") != daily_compare_key:
        try:
            st.session_state["ga4_daily_prev"] = fetch_ga4_daily_traffic(prev_mon, prev_sun)
            st.session_state["ga4_daily_curr"] = fetch_ga4_daily_traffic(curr_mon, curr_sun)
            st.session_state["ga4_daily_compare_week"] = daily_compare_key
        except Exception as exc:  # noqa: BLE001
            st.session_state["ga4_daily_prev"] = []
            st.session_state["ga4_daily_curr"] = []
            st.caption(f"요일별 방문자 데이터를 불러오지 못했습니다: {exc}")

    daily_prev = st.session_state.get("ga4_daily_prev", [])
    daily_curr = st.session_state.get("ga4_daily_curr", [])
    if daily_prev or daily_curr:
        prev_by_day = {r["weekday"]: r for r in daily_prev}
        curr_by_day = {r["weekday"]: r for r in daily_curr}
        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        fig_daily = go.Figure()
        fig_daily.add_trace(
            go.Bar(
                name=f"해당 주 · {_week_short_label(curr_mon, curr_sun)}",
                x=weekdays,
                y=[int(curr_by_day.get(d, {}).get("active_users", 0)) for d in weekdays],
                marker_color="#1a73e8",
                text=[int(curr_by_day.get(d, {}).get("active_users", 0)) for d in weekdays],
                textposition="outside",
            )
        )
        fig_daily.add_trace(
            go.Bar(
                name=f"그 전 주 · {_week_short_label(prev_mon, prev_sun)}",
                x=weekdays,
                y=[int(prev_by_day.get(d, {}).get("active_users", 0)) for d in weekdays],
                marker_color="#c9d6f0",
                text=[int(prev_by_day.get(d, {}).get("active_users", 0)) for d in weekdays],
                textposition="outside",
            )
        )
        fig_daily.update_layout(
            barmode="group",
            yaxis_title="방문자 수(명)",
            xaxis_title="요일",
            hovermode="x unified",
            margin={"l": 20, "r": 20, "t": 20, "b": 20},
        )
        st.plotly_chart(
            fig_daily,
            use_container_width=True,
            config={"responsive": True},
            key=_wk(f"ga4_daily_weekday_{curr_mon.isoformat()}"),
        )
    else:
        st.info("요일별 방문자 데이터가 없습니다.")

    if st.button(
        f"{curr_mon.year}년 {curr_mon.month}월 일별 방문자 자세히 보기",
        key=_wk(f"ga4_monthly_daily_open_{curr_mon.isoformat()}"),
    ):
        month_start = curr_mon.replace(day=1)
        if month_start.month == 12:
            next_month = date(month_start.year + 1, 1, 1)
        else:
            next_month = date(month_start.year, month_start.month + 1, 1)
        month_end = next_month - timedelta(days=1)
        with st.spinner("한 달 방문자 데이터를 불러오는 중입니다..."):
            month_rows = fetch_ga4_daily_traffic(month_start, month_end)
        _show_monthly_visitors_dialog(
            month_rows,
            f"{month_start.year}년 {month_start.month}월",
        )
    st.markdown("#### 📊 채널별 유입 — 해당 주 vs 그 전 주")
    ch_compare = ga4_weekly_report.channel_weekly_compare(curr_mon.isoformat(), prev_mon.isoformat())
    if ch_compare.empty:
        st.info("채널별 데이터가 없습니다.")
    else:
        fig = go.Figure()
        fig.add_trace(
            go.Bar(
                name=f"해당 주 · {_week_short_label(curr_mon, curr_sun)}",
                x=ch_compare["label"],
                y=ch_compare["sessions_curr"],
                marker_color="#1a73e8",
            )
        )
        fig.add_trace(
            go.Bar(
                name=f"그 전 주 · {_week_short_label(prev_mon, prev_sun)}",
                x=ch_compare["label"],
                y=ch_compare["sessions_prev"],
                marker_color="#c9d6f0",
            )
        )
        fig.update_layout(barmode="group", yaxis_title="세션수", hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True, config={"responsive": True}, key=_wk("ga4_channel_bar"))
        ch_show = ch_compare.copy()
        ch_show["채널 설명"] = ch_show["channel"].map(ga4_weekly_report.describe_channel)
        ch_disp = ch_show[
            ["label", "sessions_curr", "sessions_prev", "session_change", "active_users_curr", "채널 설명"]
        ].rename(
            columns={
                "label": "채널",
                "sessions_curr": "세션(해당 주)",
                "sessions_prev": "세션(그 전 주)",
                "session_change": "증감",
                "active_users_curr": "활성 사용자(해당 주)",
            }
        )
        st.dataframe(
            _style_week_compare(ch_disp),
            use_container_width=True,
            hide_index=True,
        )

    st.markdown("#### 📚 사람들이 본 페이지·글과 검색 유입")
    st.caption(
        "어떤 페이지와 글을 많이 봤는지, 네이버와 구글 중 어디에서 검색해 들어왔는지 보여줍니다."
    )
    ga4_content_search_auto_key = curr_mon.isoformat()
    if st.session_state.get("ga4_content_search_week") != ga4_content_search_auto_key:
        try:
            st.session_state["ga4_content_prev_rows"] = fetch_ga4_content_week(prev_mon, prev_sun)
            st.session_state["ga4_search_prev_rows"] = fetch_ga4_search_source_week(prev_mon, prev_sun)
            st.session_state["ga4_content_rows"] = fetch_ga4_content_week(curr_mon, curr_sun)
            st.session_state["ga4_search_rows"] = fetch_ga4_search_source_week(curr_mon, curr_sun)
            st.session_state["google_search_term_prev_rows"] = google_weekly_views.fetch_search_terms_week(
                prev_mon, prev_sun, 50
            )
            st.session_state["google_search_term_rows"] = google_weekly_views.fetch_search_terms_week(
                curr_mon, curr_sun, 50
            )
            st.session_state["ga4_content_search_week"] = ga4_content_search_auto_key
        except Exception as exc:  # noqa: BLE001
            st.caption(f"페이지·검색 유입 데이터를 불러오지 못했습니다: {exc}")
    if st.button(
        "페이지·검색 유입 불러오기",
        key=_wk(f"ga4_content_search_fetch_{curr_mon.isoformat()}"),
    ):
        with st.spinner("GA4에서 페이지와 검색 유입을 불러오는 중입니다..."):
            st.session_state["ga4_content_prev_rows"] = fetch_ga4_content_week(prev_mon, prev_sun)
            st.session_state["ga4_search_prev_rows"] = fetch_ga4_search_source_week(prev_mon, prev_sun)
            st.session_state["ga4_content_rows"] = fetch_ga4_content_week(curr_mon, curr_sun)
            st.session_state["ga4_search_rows"] = fetch_ga4_search_source_week(curr_mon, curr_sun)
            st.session_state["google_search_term_prev_rows"] = google_weekly_views.fetch_search_terms_week(
                prev_mon, prev_sun, 50
            )
            st.session_state["google_search_term_rows"] = google_weekly_views.fetch_search_terms_week(
                curr_mon, curr_sun, 50
            )
            st.session_state["ga4_content_search_week"] = curr_mon.isoformat()

    same_content_week = (
        st.session_state.get("ga4_content_search_week") == curr_mon.isoformat()
    )
    content_rows = st.session_state.get("ga4_content_rows", []) if same_content_week else []
    search_rows = st.session_state.get("ga4_search_rows", []) if same_content_week else []

    if content_rows:
        content_df = pd.DataFrame(content_rows)
        content_df["avg_engagement_sec"] = content_df["avg_engagement_sec"].round(1)
        content_show = content_df.rename(
            columns={
                "page_title": "페이지·글 제목",
                "page_path": "주소",
                "views": "본 횟수",
                "active_users": "본 사람 수",
                "avg_engagement_sec": "평균 관심 시간(초)",
            }
        )
        st.markdown("##### 많이 본 페이지와 글")
        st.dataframe(content_show, use_container_width=True, hide_index=True)
        st.caption(
            "본 횟수는 같은 사람이 여러 번 본 것도 포함합니다. 본 사람 수는 중복을 줄여 센 숫자입니다."
        )
    elif same_content_week:
        st.info("이 주에는 GA4에서 확인되는 페이지 조회 데이터가 없습니다.")

    if search_rows:
        search_df = pd.DataFrame(search_rows)
        engine_totals = (
            search_df.groupby("search_engine", as_index=False)["sessions"].sum()
            .set_index("search_engine")["sessions"].to_dict()
        )
        g1, g2 = st.columns(2)
        g1.metric("구글 검색으로 들어온 방문", f"{int(engine_totals.get('구글', 0)):,}회")
        g2.metric("네이버 검색으로 들어온 방문", f"{int(engine_totals.get('네이버', 0)):,}회")
        search_show = search_df.rename(
            columns={
                "search_engine": "검색 서비스",
                "search_type": "광고 여부",
                "source": "출처",
                "medium": "유입 종류",
                "channel": "GA4 분류",
                "landing_page": "처음 들어온 페이지",
                "sessions": "방문 횟수",
                "active_users": "방문한 사람 수",
            }
        )
        st.markdown("##### 네이버·구글 검색 유입")
        st.dataframe(search_show, use_container_width=True, hide_index=True)
    elif same_content_week:
        st.info("이 주에는 네이버·구글로 표시된 검색 유입이 없습니다.")

    google_term_rows = st.session_state.get("google_search_term_rows", []) if same_content_week else []
    google_term_prev_rows = (
        st.session_state.get("google_search_term_prev_rows", []) if same_content_week else []
    )
    with st.expander("실제로 입력한 구글 광고 검색어", expanded=False):
        st.caption(
            "등록해 둔 광고 키워드가 아니라, 사람이 구글 검색창에 실제로 입력한 말입니다. "
            "개인정보 보호 기준에 따라 일부 검색어는 구글이 보여주지 않을 수 있습니다."
        )
        if google_term_rows:
            term_prev = {
                str(r.get("search_term")): int(r.get("clicks", 0))
                for r in google_term_prev_rows
            }
            term_df = pd.DataFrame(google_term_rows)
            term_df["prev_clicks"] = term_df["search_term"].map(
                lambda term: term_prev.get(str(term), 0)
            )
            term_df["click_change"] = term_df["clicks"] - term_df["prev_clicks"]
            term_show = term_df.rename(
                columns={
                    "search_term": "실제 검색어",
                    "campaign_name": "캠페인",
                    "ad_group_name": "광고그룹",
                    "impressions": "노출",
                    "clicks": "이번 주 클릭",
                    "prev_clicks": "전주 클릭",
                    "click_change": "클릭 증감",
                    "conversions": "전환",
                }
            )
            st.dataframe(
                term_show[
                    [
                        "실제 검색어",
                        "캠페인",
                        "이번 주 클릭",
                        "전주 클릭",
                        "클릭 증감",
                        "노출",
                        "전환",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.info("이 주에는 구글 광고가 공개한 실제 검색어 데이터가 없습니다.")
        st.caption(
            "네이버 광고는 현재 연결 방식에서 등록 키워드 성과는 볼 수 있지만, "
            "사용자가 실제로 입력한 검색어 보고서는 아직 연결되어 있지 않습니다."
        )
    with st.expander("왜 실제 검색어는 바로 보이지 않나요?", expanded=False):
        st.markdown(
            """
GA4는 사람들이 검색창에 쓴 낱말을 대부분 알려주지 않습니다. 개인정보를 보호하기 위해서입니다.

- 구글 자연검색 검색어: Google Search Console을 연결해야 자세히 볼 수 있습니다.
- 네이버 자연검색 검색어: 네이버 서치어드바이저에서 확인해야 합니다.
- 유료 검색광고 검색어: 구글 광고·네이버 광고 화면의 검색어 보고서에서 확인합니다.

지금 추가한 표에서는 검색어 대신 어느 검색 서비스에서 들어왔는지와 처음 본 페이지를 정확하게 확인할 수 있습니다.
"""
        )
    st.markdown("---")
    unassigned_sessions = int(
        ch_compare.loc[ch_compare["channel"] == "Unassigned", "sessions_curr"].sum()
    ) if not ch_compare.empty else 0
    with st.expander(
        f"미분류 원인 자세히 보기 · 해당 주 {unassigned_sessions:,}세션",
        expanded=unassigned_sessions > 0,
    ):
        st.markdown(
            "**미분류**는 GA4가 소스/매체/캠페인 조합을 기본 채널 규칙에 "
            "넣지 못한 방문입니다. (not set)이나 비표준 매체값을 우선 확인하세요."
        )
        if st.button(
            "미분류 상세 조회",
            key=_wk(f"ga4_unassigned_fetch_{curr_mon.isoformat()}"),
            disabled=unassigned_sessions <= 0,
        ):
            with st.spinner("GA4에서 미분류 상세를 조회하는 중입니다..."):
                st.session_state["ga4_unassigned_detail"] = fetch_ga4_unassigned_detail_week(
                    curr_mon, curr_sun
                )
                st.session_state["ga4_unassigned_detail_week"] = curr_mon.isoformat()

        detail_rows = (
            st.session_state.get("ga4_unassigned_detail", [])
            if st.session_state.get("ga4_unassigned_detail_week") == curr_mon.isoformat()
            else []
        )
        if detail_rows:
            detail_df = pd.DataFrame(detail_rows).rename(
                columns={
                    "source": "세션 소스",
                    "medium": "세션 매체",
                    "campaign": "캠페인",
                    "landing_page": "첫 진입 페이지",
                    "sessions": "세션",
                    "active_users": "활성 사용자",
                }
            )
            st.dataframe(detail_df, use_container_width=True, hide_index=True)
            st.download_button(
                "미분류 상세 CSV 내려받기",
                data=detail_df.to_csv(index=False).encode("utf-8-sig"),
                file_name=f"ga4_unassigned_{curr_mon.isoformat()}.csv",
                mime="text/csv",
                key=_wk(f"ga4_unassigned_dl_{curr_mon.isoformat()}"),
            )
        elif unassigned_sessions > 0:
            st.info("‘미분류 상세 조회’를 누르면 소스·매체·캠페인·첫 진입 페이지가 표시됩니다.")
        else:
            st.success("해당 주에는 미분류 세션이 없습니다.")

        st.markdown(
            """
**확인 후 조치 순서**

1. medium이 (not set)이면 광고·문자·카카오·외부 링크에 UTM을 추가합니다.
2. 유료 검색 링크의 utm_medium은 cpc로 통일합니다.
3. 캠페인이 (not set)이면 utm_campaign 또는 Google Ads 자동 태그 연결을 확인합니다.
4. 특정 첫 진입 페이지만 몰리면 해당 페이지로 연결되는 버튼·QR·리디렉션을 점검합니다.
"""
        )
    st.markdown("#### 🎯 주요 전환 이벤트 — 해당 주 vs 그 전 주")
    st.caption(
        "GTM 커스텀 이벤트: 로톡 상담 클릭 · 카카오톡 문의 클릭 · 전문분야 클릭 · "
        "변호사 소개 5초 체류 · AI 상담 전송 클릭"
    )
    ev_compare = ga4_weekly_report.key_events_compare(curr_mon.isoformat(), prev_mon.isoformat())
    if ev_compare.empty:
        st.info("이벤트 데이터가 없습니다.")
    else:
        ev_show = ev_compare.copy()
        ev_show["의미"] = ev_show["event_name"].map(ga4_weekly_report.describe_event)
        ev_disp = ev_show[["label", "count_curr", "count_prev", "change", "의미"]].rename(
            columns={
                "label": "이벤트",
                "count_curr": "해당 주",
                "count_prev": "그 전 주",
                "change": "증감",
            }
        )
        st.dataframe(
            _style_week_compare(ev_disp),
            use_container_width=True,
            hide_index=True,
        )
        if int(ev_compare["count_curr"].sum()) <= 0:
            st.info(
                "이 주에는 주요 전환 이벤트가 0건으로 집계됐습니다 — GTM 태그가 정상 게시됐는지, "
                "충분한 방문이 있었는지 확인하세요."
            )

    st.markdown("#### 🤖 상담안내AI 사용 현황")
    st.caption("페이지 조회(진입)와 AI 상담 전송 클릭(실사용)을 함께 봅니다. 경로: `/AIcounselor`")
    _usage_fn = getattr(ga4_weekly_report, "ai_counselor_usage", None)
    if _usage_fn is None:
        st.warning("상담안내AI 모듈이 아직 로드되지 않았습니다. 페이지를 새로고침하거나 서버를 재시작해 주세요.")
        ai_usage = {
            "page_views_curr": 0,
            "page_views_prev": 0,
            "send_clicks_curr": 0,
            "send_clicks_prev": 0,
            "usage_rate_curr": None,
            "usage_rate_prev": None,
            "avg_engagement_sec_curr": 0.0,
            "avg_engagement_sec_prev": 0.0,
        }
    else:
        ai_usage = _usage_fn(curr_mon.isoformat(), prev_mon.isoformat())

    with st.container(border=True):
        a1, a2, a3, a4 = st.columns(4)
        a1.metric(
            "페이지 조회",
            f"{int(ai_usage.get('page_views_curr') or 0):,}",
            delta=int((ai_usage.get("page_views_curr") or 0) - (ai_usage.get("page_views_prev") or 0)),
        )
        a2.metric(
            "전송 클릭",
            f"{int(ai_usage.get('send_clicks_curr') or 0):,}",
            delta=int((ai_usage.get("send_clicks_curr") or 0) - (ai_usage.get("send_clicks_prev") or 0)),
        )
        rate = ai_usage.get("usage_rate_curr")
        rate_prev = ai_usage.get("usage_rate_prev")
        a3.metric(
            "조회 대비 실사용률",
            f"{rate:.1f}%" if rate is not None else "–",
            delta=(f"{(rate or 0) - (rate_prev or 0):+.1f}%p" if rate is not None and rate_prev is not None else None),
        )
        eng = float(ai_usage.get("avg_engagement_sec_curr") or 0)
        eng_prev = float(ai_usage.get("avg_engagement_sec_prev") or 0)
        a4.metric(
            "평균 참여 시간",
            f"{eng:.0f}초",
            delta=f"{eng - eng_prev:+.0f}초",
        )
        if int(ai_usage.get("page_views_curr") or 0) > 0 and int(ai_usage.get("send_clicks_curr") or 0) == 0:
            st.warning(
                "페이지 조회는 있지만 전송 클릭이 0건입니다. "
                "진입만 하고 메시지를 보내지 않았거나, `ai_counselor_send_click` GTM 태그를 점검하세요."
            )

    with st.expander("전체 이벤트 목록 (해당 주)", expanded=False):
        all_ev = ga4_weekly_report.events_df(curr_mon.isoformat())
        if all_ev.empty:
            st.caption("데이터 없음.")
        else:
            all_show = all_ev.copy()
            all_show["의미"] = all_show["event_name"].map(ga4_weekly_report.describe_event)
            st.dataframe(
                all_show[["label", "event_count", "의미"]].rename(
                    columns={"label": "이벤트", "event_count": "발생 수"}
                ),
                use_container_width=True,
                hide_index=True,
            )

    hist_weeks = max(8, min(26, int(config.SYNC_LOOKBACK_DAYS) // 7))
    hist_df = ga4_weekly_report.weekly_history_table(max_weeks=hist_weeks)
    with st.expander(f"저장된 주간 기록 ({hist_weeks}주)", expanded=False):
        if hist_df.empty:
            st.caption("아직 저장된 주간 스냅샷이 없습니다.")
        else:
            st.dataframe(
                hist_df[["월~일", "세션", "활성 사용자", "1위 채널", "수집"]],
                use_container_width=True,
                hide_index=True,
            )

    with st.expander("용어 · 채널 · GTM 태그 해석", expanded=False):
        gloss = ga4_weekly_report.glossary_dataframe()
        st.dataframe(gloss, use_container_width=True, hide_index=True)

    st.markdown("#### 💡 데이터 해석 · 전주 대비")
    st.caption("위 표·차트를 바탕으로 한 자동 해석입니다. 조회 주를 바꾸면 비교 구간도 함께 바뀝니다.")
    try:
        analysis = ga4_weekly_report.build_homepage_analysis(prev_mon, prev_sun, curr_mon, curr_sun)
        with st.container(border=True):
            for line in analysis:
                st.markdown(f"- {line}")

        detail_lines: list[str] = []
        page_table_rows: list[dict[str, object]] = []
        case_summary_rows: list[dict[str, object]] = []
        case_table_rows: list[dict[str, object]] = []
        search_table_rows: list[dict[str, object]] = []
        report_daily_curr = st.session_state.get("ga4_daily_curr", [])
        report_daily_prev = st.session_state.get("ga4_daily_prev", [])
        if report_daily_curr:
            busy = max(report_daily_curr, key=lambda r: int(r.get("active_users", 0)))
            prev_same = next(
                (r for r in report_daily_prev if r.get("weekday") == busy.get("weekday")),
                {},
            )
            detail_lines.append(
                f"사람이 가장 많이 들어온 날은 **{busy.get('weekday')}요일**로 "
                f"**{int(busy.get('active_users', 0)):,}명**입니다. "
                f"전주의 같은 요일은 **{int(prev_same.get('active_users', 0)):,}명**이었습니다."
            )

        report_content = st.session_state.get("ga4_content_rows", [])
        report_content_prev = st.session_state.get("ga4_content_prev_rows", [])
        if report_content:
            prev_views = {
                str(r.get("page_path")): int(r.get("views", 0))
                for r in report_content_prev
            }
            for rank, row in enumerate(report_content[:3], start=1):
                curr_views = int(row.get("views", 0))
                before_views = prev_views.get(str(row.get("page_path")), 0)
                page_table_rows.append(
                    {
                        "순위": rank,
                        "페이지·글 제목": row.get("page_title") or row.get("page_path"),
                        "이번 주": curr_views,
                        "전주": before_views,
                        "증감": curr_views - before_views,
                    }
                )

        case_interest = homepage_content.summarize_case_views(
            report_content,
            report_content_prev,
        )
        case_totals = case_interest.get("totals", {})
        for case_type in ("상담사례", "승소사례"):
            values = case_totals.get(case_type, {})
            views_curr = int(values.get("views_curr", 0))
            views_prev = int(values.get("views_prev", 0))
            users_case = int(values.get("users_curr", 0))
            articles_case = int(values.get("articles_curr", 0))
            if views_curr > 0 or views_prev > 0:
                case_summary_rows.append(
                    {
                        "구분": case_type,
                        "글 수": articles_case,
                        "이번 주 조회": views_curr,
                        "전주 조회": views_prev,
                        "증감": views_curr - views_prev,
                        "글별 방문자 합계": users_case,
                    }
                )

        for item in case_interest.get("top_items", []):
            case_table_rows.append(
                {
                    "구분": item.get("case_type"),
                    "분야": item.get("field") or "-",
                    "제목": item.get("title"),
                    "이번 주 조회": int(item.get("views", 0)),
                    "본 사람 수": int(item.get("active_users", 0)),
                    "전주 대비": int(item.get("view_change", 0)),
                }
            )
        report_search = st.session_state.get("ga4_search_rows", [])
        report_search_prev = st.session_state.get("ga4_search_prev_rows", [])
        if report_search:
            curr_engine: dict[str, int] = {}
            prev_engine: dict[str, int] = {}
            for row in report_search:
                name = str(row.get("search_engine"))
                curr_engine[name] = curr_engine.get(name, 0) + int(row.get("sessions", 0))
            for row in report_search_prev:
                name = str(row.get("search_engine"))
                prev_engine[name] = prev_engine.get(name, 0) + int(row.get("sessions", 0))
            for engine in ("구글", "네이버"):
                now = curr_engine.get(engine, 0)
                before = prev_engine.get(engine, 0)
                search_table_rows.append(
                    {"검색 서비스": engine, "이번 주 유입": now, "전주 유입": before, "증감": now - before}
                )

        if detail_lines or page_table_rows or case_summary_rows or case_table_rows or search_table_rows:
            with st.container(border=True):
                st.markdown("##### 이번 주의 자세한 특징")
                st.caption("이번 주와 전주를 표로 나란히 비교합니다. 초록색은 증가, 빨간색은 감소입니다.")
                for line in detail_lines:
                    st.markdown(f"- {line}")
                if page_table_rows:
                    st.markdown("###### 많이 본 페이지·글 TOP 3")
                    st.dataframe(_style_week_compare(pd.DataFrame(page_table_rows)), use_container_width=True, hide_index=True)
                if case_summary_rows:
                    st.markdown("###### 상담사례·승소사례 전체 요약")
                    st.dataframe(_style_week_compare(pd.DataFrame(case_summary_rows)), use_container_width=True, hide_index=True)
                    st.caption("글별 방문자 합계는 한 사람이 여러 글을 읽으면 중복될 수 있습니다.")
                if case_table_rows:
                    st.markdown("###### 많이 본 상담사례·승소사례")
                    st.dataframe(_style_week_compare(pd.DataFrame(case_table_rows)), use_container_width=True, hide_index=True)
                if search_table_rows:
                    st.markdown("###### 구글·네이버 검색 유입")
                    st.dataframe(_style_week_compare(pd.DataFrame(search_table_rows)), use_container_width=True, hide_index=True)
    except Exception as exc:  # noqa: BLE001
        st.caption(f"해석 생성 중 오류: {exc}")

    st.markdown("---")



def _fmt_naver_short(d) -> str:
    return f"{d.month:02d}.{d.day:02d}"


def render_dashboard_main_only() -> None:
    """메인 패널 — 조회 주·섹션은 사이드바 session_state 참조."""
    if _current_page() == "case_admin":
        case_admin.render_case_admin()
        return
    if _current_page() == "winning_case_admin":
        winning_case_admin.render_winning_case_admin()
        return
    _inject_print_css()
    labels, label_to_offset = _dash_week_lookups()
    sel_week = st.session_state.get("dash_week_pick")
    if sel_week not in label_to_offset:
        sel_week = labels[0] if labels else ""
    week_off = label_to_offset.get(sel_week, -1)

    lookback = max(21, max(7, int(config.SYNC_LOOKBACK_DAYS)))
    ms, me = time_utils.last_n_days_range(lookback)

    # 툴바(동기화)를 먼저 그려, 동기화가 실행되면 아래 데이터 로드가 최신 DB를 읽도록 한다.
    _render_main_toolbar()

    merged_all = merge.load_merged_range(ms, me)

    show_any_week_section = any(
        _show_section(k) for k in ("hero", "naver_week_report", "google_week_report", "ga4_traffic")
    )
    if show_any_week_section:
        _, _, curr_mon, curr_sun = time_utils.calendar_week_bounds(week_off)
        _, _, prev_mon, prev_sun = time_utils.calendar_week_bounds(week_off - 1)
        _render_week_period_banner(
            heading="주간 비교 구간",
            this_mon=curr_mon,
            this_sun=curr_sun,
            prev_mon=prev_mon,
            prev_sun=prev_sun,
            note="아래 모든 섹션(주간 브리핑·네이버·구글·홈페이지)이 이 구간을 기준으로 비교합니다.",
        )

    if _show_section("hero"):
        _render_weekly_report_hero(merged_all)

    if _show_section("naver_week_report"):
        _render_naver_weekly_comparison_report()

    if _show_section("google_week_report"):
        _render_google_weekly_comparison_report()

    if _show_section("ga4_traffic"):
        _render_ga4_traffic_report()

    if _show_section("gemini_ai"):
        _render_gemini_weekly_analysis()

    w_start, w_end, d_mon, d_sun = time_utils.calendar_week_bounds(week_off)
    merged = (
        merged_all[(merged_all["ts_hour"] >= w_start) & (merged_all["ts_hour"] <= w_end)].copy()
        if not merged_all.empty
        else merged_all
    )

    t0, _ = time_utils.today_range_iso()
    merged_today = merged_all[merged_all["ts_hour"] >= t0] if not merged_all.empty else merged_all
    totals_today = merge.today_totals(merged_today)
    totals_week = merge.period_totals(merged)
    wap = merge.week_ad_performance(merged, week_start=d_mon.isoformat())
    snap_sel = naver_weekly_report.week_totals(d_mon.isoformat())
    if snap_sel.get("has_snapshot"):
        naver_week = {
            "naver_cost": float(snap_sel["cost"]),
            "naver_clicks": float(snap_sel["clicks"]),
            "naver_impressions": float(snap_sel["impressions"]),
            "naver_cpc": float(snap_sel["cpc"]),
        }
    else:
        naver_week = merge.naver_week_totals(merged)
    google_week = merge.google_week_totals(merged)

    if _show_section("selected_kpi"):
        st.subheader("통합 광고 KPI · 선택 주")
        st.caption(f"**해당 주** · {time_utils.format_week_range(d_mon, d_sun)}")
        if week_off == -1 and _show_section("hero"):
            st.info("지난 완료 주는 위 **① 주간 브리핑**과 동일합니다. 채널·세션만 아래에 요약합니다.")
        elif wap["total_clicks"] <= 0 and wap["total_impressions"] <= 0:
            st.warning(
                "선택 주 광고 클릭/노출이 0입니다. API 미수집 또는 데이터 누락 가능성이 있어 해석에 주의하세요."
            )
        if not (week_off == -1 and _show_section("hero")):
            k1, k2, k3, k4, k5 = st.columns(5)
            k1.metric("통합 클릭", f"{int(wap['total_clicks']):,}")
            k2.metric("블렌드 CTR", f"{wap['blended_ctr_pct']:.2f}%")
            k3.metric("블렌드 CPC", f"{wap['blended_cpc']:,.0f} 원" if wap["blended_cpc"] else "–")
            k4.metric("통합 광고비", f"{wap['total_cost']:,.0f} 원")
            k5.metric("ROAS", f"{totals_week['roas']:.2f}x")
        sess_w = int(totals_week["total_sessions"])
        cps = (totals_week["total_cost"] / sess_w) if sess_w else None
        c1, c2, c3 = st.columns(3)
        c1.metric("세션", f"{sess_w:,}")
        c2.metric("세션당 광고비", f"{cps:,.0f} 원" if cps is not None else "–")
        c3.metric("네이버/구글 클릭", f"{int(naver_week['naver_clicks']):,} / {int(google_week['google_clicks']):,}")
        st.markdown("---")

    if _show_section("weekly_trend"):
        st.subheader("주간 추이 (통합 클릭 · 비용)")
        st.caption("클릭과 비용은 **별도 패널·별도 Y축**으로 표시합니다. 숫자는 막대 위에 표기됩니다.")
        trend_weeks = max(4, min(lookback // 7, 12))
        tdf = _weekly_trend_table(merged_all, max_weeks=trend_weeks)
        trend_rows = tdf.drop(columns=["offset"], errors="ignore").to_dict("records")
        fig_trend = charts.integrated_week_trend_plotly(trend_rows)
        if fig_trend is not None:
            st.plotly_chart(fig_trend, use_container_width=True, config={"responsive": True})
        else:
            st.warning("주간 추이 데이터가 없습니다. 「전체 동기화」를 실행하세요.")
        with st.expander("주차별 숫자 표 · CSV", expanded=False):
            st.dataframe(
                tdf.drop(columns=["offset"], errors="ignore"),
                use_container_width=True,
                hide_index=True,
            )
            st.download_button(
                label="주간 추이표 CSV 다운로드",
                data=tdf.drop(columns=["offset"], errors="ignore").to_csv(index=False).encode("utf-8-sig"),
                file_name="weekly_ad_trend.csv",
                mime="text/csv",
                key=_wk("dl_weekly_trend"),
            )
        st.markdown("---")

    if _show_section("today_snap"):
        st.subheader("오늘 스냅샷 (참고)")
        wt = merge.week_ad_performance(merged_today)
        c1, c2, c3 = st.columns(3)
        c1.metric("오늘 총 광고비", f"{totals_today['total_cost']:,.0f} 원")
        c2.metric("오늘 통합 클릭", f"{int(wt['total_clicks']):,}")
        c3.metric("오늘 세션", f"{int(totals_today['total_sessions']):,}")
        c4, c5 = st.columns(2)
        c4.metric("오늘 활성 사용자", f"{totals_today['total_active_users']:,.1f}")
        c5.metric("오늘 ROAS", f"{totals_today['roas']:.2f}x")
        st.caption(
            "일 단위는 변동이 큽니다. **주간 보고의 기본은 위의 통합 KPI·히어로**를 쓰는 것을 권장합니다."
        )
        st.markdown("---")

    if _show_section("naver_weekly"):
        st.subheader("네이버 검색광고 — 선택 주 상세")
        prev_sel_mon = d_mon - timedelta(days=7)
        prev_sel_sun = d_sun - timedelta(days=7)
        st.caption(f"**해당 주** · {time_utils.format_week_range(d_mon, d_sun)}")
        n1, n2, n3, n4, n5 = st.columns(5)
        n1.metric("네이버 광고비", f"{naver_week['naver_cost']:,.0f} 원")
        n2.metric("클릭", f"{int(naver_week['naver_clicks']):,}")
        n3.metric("노출", f"{int(naver_week['naver_impressions']):,}")
        n4.metric("CTR", f"{wap['naver_ctr_pct']:.2f}%")
        n5.metric("평균 CPC", f"{naver_week['naver_cpc']:,.0f} 원" if naver_week["naver_cpc"] else "-")

        report_same_week = week_off == -1 and _show_section("naver_week_report")
        if report_same_week:
            st.info("캠페인·키워드 주간 비교는 **② 네이버 주간 성과**에 원형·막대 차트와 함께 있습니다.")
        else:
            _render_naver_visual_charts(
                d_mon, d_sun, prev_sel_mon, prev_sel_sun, key_prefix="selected_week"
            )
            _render_campaign_weekly_compare(
                d_mon, d_sun, prev_sel_mon, prev_sel_sun, key_prefix="selected_week", show_caption=False
            )
            _render_keyword_top20_compare(
                d_mon, d_sun, prev_sel_mon, prev_sel_sun, key_prefix="selected_week", show_caption=False
            )

        if st.button(
            f"선택 주 데이터 새로고침 · {d_mon}",
            key=_wk(f"requery_one_{d_mon.isoformat()}"),
        ):
            with st.spinner("선택 주 네이버 데이터 재조회 중…"):
                one = naver_weekly_views.sync_naver_week_one(d_mon.isoformat())
            st.session_state["last_naver_week_single_sync"] = one
            st.rerun()
        one = st.session_state.get("last_naver_week_single_sync")
        if isinstance(one, dict) and one.get("week_start") == d_mon.isoformat() and one.get("ok"):
            st.success(
                f"재조회 완료 — 클릭 {int(one.get('total_clicks') or 0):,} · "
                f"비용 {int(one.get('total_cost') or 0):,}원"
            )

        st.markdown("---")
        st.subheader("주간 수집 이력")
        snaps = db.fetch_naver_week_snapshots()
        if not snaps:
            st.info("저장된 주간 데이터가 없습니다. 상단 「네이버 주간 수집」을 실행해 주세요.")
        else:
            sdf = pd.DataFrame([dict(s) for s in snaps]).sort_values("week_start").reset_index(drop=True)
            if "total_clicks" not in sdf.columns:
                sdf["total_clicks"] = 0
            if "total_cost" not in sdf.columns:
                sdf["total_cost"] = 0.0
            sdf["total_clicks"] = sdf["total_clicks"].fillna(0).astype(int)
            sdf["total_cost"] = sdf["total_cost"].fillna(0.0)
            sdf["구간"] = sdf["week_start"].astype(str) + " ~ " + sdf["week_end"].astype(str)
            fig_n = make_subplots(
                rows=2,
                cols=1,
                shared_xaxes=True,
                vertical_spacing=0.14,
                subplot_titles=("주간 총 클릭 (캠페인 합계)", "주간 총 노출"),
            )
            fig_n.add_trace(
                go.Bar(x=sdf["구간"], y=sdf["total_clicks"], name="클릭", marker_color="#1a73e8"),
                row=1,
                col=1,
            )
            fig_n.add_trace(
                go.Bar(x=sdf["구간"], y=sdf["total_impressions"], name="노출", marker_color="#7cb342"),
                row=2,
                col=1,
            )
            fig_n.update_xaxes(tickangle=-28)
            fig_n.update_layout(showlegend=False, margin=dict(l=40, r=20, t=52, b=44))
            st.plotly_chart(fig_n, use_container_width=True, config={"responsive": True})

            tbl = sdf[["구간", "total_clicks", "total_cost", "total_impressions", "synced_at"]].rename(
                columns={
                    "total_clicks": "총 클릭",
                    "total_cost": "총 비용(원)",
                    "total_impressions": "총 노출",
                    "synced_at": "수집 시각",
                }
            )
            st.dataframe(tbl, use_container_width=True, hide_index=True)
            zero_cost_weeks = tbl[tbl["총 비용(원)"] <= 0]["구간"].tolist()
            if zero_cost_weeks:
                st.warning(
                    "비용 0 주차(구버전 수집): "
                    + ", ".join(zero_cost_weeks[:4])
                    + " · 재조회 필요."
                )
            zero_click_weeks = tbl[tbl["총 클릭"] <= 0]["구간"].tolist()
            if zero_click_weeks:
                st.warning(
                    "클릭 0으로 저장된 주차가 있습니다: "
                    + ", ".join(zero_click_weeks[:4])
                    + (" ..." if len(zero_click_weeks) > 4 else "")
                    + " · 해당 주차를 선택해 재조회 버튼을 눌러주세요."
                )
            selected_ws = d_mon.isoformat()
            if selected_ws not in set(sdf["week_start"].astype(str)):
                st.warning(
                    f"선택 주 `{selected_ws} ~ {d_sun.isoformat()}` 는 네이버 주간 스냅샷이 없어 "
                    "캠페인/키워드 상세가 비어 있을 수 있습니다."
                )

            pick_rows = sdf.sort_values("week_start", ascending=False)
            pick_labels = []
            for _, r in pick_rows.iterrows():
                tk = int(r["total_clicks"])
                ti = int(r["total_impressions"])
                pick_labels.append(f"{r['week_start']} ~ {r['week_end']} · 클릭 {tk:,} · 노출 {ti:,}")
            label_to_start = {pick_labels[i]: str(pick_rows.iloc[i]["week_start"]) for i in range(len(pick_labels))}
            sel_lbl = st.selectbox("세부: 캠페인별", pick_labels, index=0)
            ws = label_to_start[sel_lbl]
            ctab = pd.DataFrame([dict(x) for x in db.fetch_naver_week_campaign_views(ws)])
            if not ctab.empty:
                # Display KRW as whole won with thousands separators (e.g. 350,678),
                # instead of SQLite/Pandas' default four-decimal representation.
                if "cost" in ctab.columns:
                    ctab["cost"] = pd.to_numeric(ctab["cost"], errors="coerce").fillna(0).map(
                        lambda value: f"{value:,.0f}"
                    )
                ctab = ctab.rename(
                    columns={
                        "campaign_name": "캠페인",
                        "cost": "비용(원)",
                        "impressions": "노출",
                        "clicks": "클릭",
                    }
                )
                if "클릭" not in ctab.columns:
                    ctab["클릭"] = 0
                if "노출" not in ctab.columns:
                    ctab["노출"] = 0
                if "비용(원)" not in ctab.columns:
                    ctab["비용(원)"] = 0
                imp_c = ctab["노출"].clip(lower=0)
                clk_c = ctab["클릭"].clip(lower=0)
                ctab["CTR(%)"] = [
                    round(100.0 * float(c) / float(i), 2) if i else 0.0 for i, c in zip(imp_c, clk_c)
                ]
                ctab = ctab.drop(columns=["week_start", "ncc_campaign_id"], errors="ignore")
                col_order = [
                    c for c in ("캠페인", "비용(원)", "클릭", "노출", "CTR(%)") if c in ctab.columns
                ]
                ctab = ctab[col_order]
            st.markdown("###### 캠페인별 · 클릭 / 노출 / CTR")
            if ctab.empty:
                st.caption("캠페인별 데이터 없음.")
            else:
                st.table(ctab)

            st.markdown("###### 주간 키워드 TOP 20 비교")
            st.caption("해당 주 **클릭 수** 기준 상위 20개를 주차별로 나란히 비교합니다.")
            wk_options = [
                f"{r['week_start']} ~ {r['week_end']}"
                for _, r in pick_rows.iterrows()
            ]
            wk_label_to_start = {
                f"{r['week_start']} ~ {r['week_end']}": str(r["week_start"])
                for _, r in pick_rows.iterrows()
            }
            default_weeks = wk_options[:2] if len(wk_options) >= 2 else wk_options[:1]
            selected_weeks = st.multiselect(
                "비교할 주차 (최대 3개 권장)",
                options=wk_options,
                default=default_weeks,
                key=_wk("kw_top10_compare_weeks"),
            )
            if not selected_weeks:
                st.info("비교할 주차를 1개 이상 선택하세요.")
            else:
                if st.button("선택 주차 재조회 (API)", key=_wk("kw_top10_requery_weeks")):
                    targets = [wk_label_to_start[w] for w in selected_weeks]
                    outs: list[dict[str, object]] = []
                    with st.spinner("선택 주차 네이버 스냅샷 재조회 중…"):
                        for ws1 in targets:
                            outs.append(naver_weekly_views.sync_naver_week_one(ws1))
                    st.session_state["last_naver_kw_compare_requery"] = outs
                    st.rerun()

                re_hist = st.session_state.get("last_naver_kw_compare_requery")
                if isinstance(re_hist, list) and re_hist:
                    ok_n = sum(1 for r in re_hist if bool(r.get("ok")))
                    err_n = len(re_hist) - ok_n
                    if err_n:
                        st.warning(f"직전 선택 주차 재조회: {ok_n}개 성공 / {err_n}개 실패")
                    else:
                        st.success(f"직전 선택 주차 재조회: {ok_n}개 모두 성공")

                if len(selected_weeks) > 3:
                    st.warning("가독성을 위해 앞의 3개 주차만 표시합니다.")
                show_weeks = selected_weeks[:3]
                week_cols = st.columns(len(show_weeks))
                top10_by_week: dict[str, pd.DataFrame] = {}
                for idx, lbl in enumerate(show_weeks):
                    week_start = wk_label_to_start[lbl]
                    wk_df = pd.DataFrame([dict(x) for x in db.fetch_naver_week_keyword_top(week_start)])
                    if wk_df.empty:
                        with week_cols[idx]:
                            st.markdown(f"**{lbl}**")
                            st.caption("키워드 데이터 없음")
                        continue
                    wk_df = wk_df.rename(
                        columns={
                            "rank": "순위",
                            "keyword": "키워드",
                            "campaign_name": "캠페인",
                            "impressions": "노출",
                            "clicks": "클릭",
                        }
                    )
                    wk_df = wk_df.sort_values(["클릭", "노출"], ascending=[False, False]).head(20).reset_index(drop=True)
                    wk_df["순위"] = range(1, len(wk_df) + 1)
                    imp = wk_df["노출"].clip(lower=0)
                    clk = wk_df["클릭"].clip(lower=0)
                    wk_df["CTR(%)"] = [round(100.0 * float(c) / float(i), 2) if i else 0.0 for i, c in zip(imp, clk)]
                    out_cols = [c for c in ["순위", "키워드", "클릭", "노출", "CTR(%)", "캠페인"] if c in wk_df.columns]
                    top10_by_week[week_start] = wk_df[out_cols].copy()
                    with week_cols[idx]:
                        st.markdown(f"**{lbl}**")
                        st.table(top10_by_week[week_start])

                if len(show_weeks) == 2:
                    wa = wk_label_to_start[show_weeks[0]]
                    wb = wk_label_to_start[show_weeks[1]]
                    kw_cmp = naver_weekly_report.keyword_clicks_compare(wa, wb, top_n=20)
                    if not kw_cmp.empty:
                        wa_mon = date.fromisoformat(wa)
                        wb_mon = date.fromisoformat(wb)
                        st.markdown("**두 주 클릭 증감 (TOP 20)**")
                        st.caption(
                            _week_compare_caption(
                                wa_mon, wa_mon + timedelta(days=6),
                                wb_mon, wb_mon + timedelta(days=6),
                            )
                        )
                        cmp_show = kw_cmp.rename(
                            columns={
                                "rank": "순위",
                                "keyword": "키워드",
                                "clicks_curr": "클릭(해당 주)",
                                "clicks_prev": "클릭(그 전 주)",
                                "click_change": "증감",
                            }
                        )[["순위", "키워드", "클릭(해당 주)", "클릭(그 전 주)", "증감"]]
                        st.table(cmp_show)
                    dfa = top10_by_week.get(wa)
                    dfb = top10_by_week.get(wb)
                    if dfa is not None and dfb is not None and not dfa.empty and not dfb.empty:
                        ka = set(dfa["키워드"].astype(str))
                        kb = set(dfb["키워드"].astype(str))
                        inter = sorted(list(ka & kb))
                        only_a = len(ka - kb)
                        only_b = len(kb - ka)
                        st.caption(
                            f"두 주차 TOP20 겹치는 키워드: {len(inter)}개 · "
                            f"{show_weeks[0]} 고유 {only_a}개 · {show_weeks[1]} 고유 {only_b}개"
                        )
                        if inter:
                            st.write("공통 키워드:", ", ".join(inter[:8]) + (" ..." if len(inter) > 8 else ""))

        st.markdown("---")

    if _show_section("campaign_notes"):
        st.subheader("캠페인별 특이사항 (선택 주 · 네이버)")
        st.markdown(f"**분석 구간:** {time_utils.format_week_range(d_mon, d_sun)}")
        campaign_rows = db.fetch_naver_week_campaign_views(d_mon.isoformat())
        cdf = pd.DataFrame([dict(x) for x in campaign_rows])
        if cdf.empty:
            st.warning(
                "선택 주 캠페인 데이터가 없습니다. 상단 「🔄 전체 동기화」를 실행하면 채워집니다."
            )
        else:
            notes = _campaign_special_notes(cdf)
            st.markdown("\n".join(f"- {x}" for x in notes))
            cdf = cdf.rename(
                columns={
                    "campaign_name": "캠페인",
                    "clicks": "클릭",
                    "impressions": "노출",
                    "ncc_campaign_id": "캠페인 ID",
                }
            )
            if "클릭" not in cdf.columns:
                cdf["클릭"] = 0
            if "노출" not in cdf.columns:
                cdf["노출"] = 0
            cdf["CTR(%)"] = [
                round(100.0 * float(c) / float(i), 2) if float(i) > 0 else 0.0
                for c, i in zip(cdf["클릭"], cdf["노출"])
            ]
            cdf = cdf.sort_values(["클릭", "노출"], ascending=False).reset_index(drop=True)
            st.dataframe(
                cdf[[c for c in ["캠페인", "클릭", "노출", "CTR(%)", "캠페인 ID"] if c in cdf.columns]],
                use_container_width=True,
                hide_index=True,
            )
        st.markdown("---")

    if _show_section("spend_sessions"):
        st.subheader("지출 vs 방문자 (선택 주)")
        if merged.empty:
            st.info("선택 주 데이터가 없습니다. 동기화를 실행하세요.")
        else:
            daily_chart = merged.copy()
            daily_chart["날짜"] = pd.to_datetime(daily_chart["ts_hour"]).dt.strftime("%m월 %d일")
            daily_chart = daily_chart.groupby("날짜", as_index=False).agg(
                광고비=("total_cost", "sum"), 세션=("sessions", "sum")
            )
            fig1 = go.Figure()
            fig1.add_trace(
                go.Bar(
                    x=daily_chart["날짜"], y=daily_chart["광고비"], name="광고비", yaxis="y",
                    text=[f"{v:,.0f}원" for v in daily_chart["광고비"]], textposition="outside",
                    hovertemplate="%{x}<br>광고비 %{y:,.0f}원<extra></extra>",
                )
            )
            fig1.add_trace(
                go.Scatter(
                    x=daily_chart["날짜"], y=daily_chart["세션"], name="세션", yaxis="y2", mode="lines+markers+text",
                    text=[f"{int(v):,}회" for v in daily_chart["세션"]], textposition="top center",
                    hovertemplate="%{x}<br>세션 %{y:,.0f}회<extra></extra>",
                )
            )
            fig1.update_layout(
                height=560,
                font=dict(size=15),
                yaxis=dict(title="광고비 (원)", side="left", tickformat=",.0f", ticksuffix="원"),
                yaxis2=dict(title="세션 (회)", overlaying="y", side="right", showgrid=False, tickformat=",.0f", ticksuffix="회"),
                xaxis=dict(title="날짜", tickfont=dict(size=14)),
                hovermode="x unified",
                bargap=0.32,
            )
            plotly_responsive(fig1)
            with st.expander("같은 기간 · 시간별 통합 클릭", expanded=False):
                fig_c = go.Figure()
                fig_c.add_trace(
                    go.Scatter(
                        x=merged["ts_hour"],
                        y=merged["total_clicks"],
                        name="통합 클릭",
                        mode="lines+markers",
                        line=dict(color="#1a73e8"),
                    )
                )
                fig_c.update_layout(yaxis_title="클릭", hovermode="x unified")
                plotly_responsive(fig_c)
        st.markdown("---")

    if _show_section("alerts"):
        st.subheader("이상 징후 알림 로그")
        rows = db.fetch_recent_alerts(80)
        if not rows:
            st.caption("기록된 알림이 없습니다.")
        else:
            for r in rows:
                st.write(f"**{r['created_at']}** · `{r['alert_type']}` — {r['message']}")
def main() -> None:
    if "logged_in" not in st.session_state:
        st.session_state["logged_in"] = False

    if not getattr(config, "LOGIN_REQUIRED", False):
        st.session_state["logged_in"] = True

    if not st.session_state["logged_in"]:
        login_form()
        return

    if st.sidebar.button("로그아웃"):
        st.session_state["logged_in"] = False
        st.rerun()

    render_dashboard_sidebar_only()
    render_dashboard_main_only()


if __name__ == "__main__":
    main()
