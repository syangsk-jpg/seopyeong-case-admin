"""주간 광고 결과 — 가로형(PPT 스타일) PDF 보고서."""
from __future__ import annotations

import io
from datetime import date
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

import charts
import config
import ga4_weekly_report
import google_weekly_report
import merge
import naver_weekly_report
import time_utils

# 16:9에 가까운 가로 슬라이드 (pt)
PAGE_W, PAGE_H = landscape((33.867 * cm, 19.05 * cm))
MARGIN = 1.0 * cm

_FONT_REG = "KR"
_FONT_BOLD = "KRB"
_FONTS_OK = False

# ── 타이포 (PDF 가독성 — 슬라이드 꽉 채움) ──
FS_COVER_TITLE = 40
FS_COVER_SUB = 16
FS_SLIDE_TITLE = 28
FS_SLIDE_SUB = 13
FS_TABLE_HEADER = 14
FS_TABLE_BODY = 13
FS_TABLE_LEADING = 17
FS_KPI_LABEL = 13
FS_KPI_VALUE = 28
FS_BULLET = 14
FS_FRAME = 11


def _register_fonts() -> None:
    global _FONTS_OK
    if _FONTS_OK:
        return
    candidates = [
        (Path(r"C:/Windows/Fonts/malgun.ttf"), _FONT_REG),
        (Path(r"C:/Windows/Fonts/malgunbd.ttf"), _FONT_BOLD),
        (Path(r"C:/Windows/Fonts/NanumGothic.ttf"), _FONT_REG),
        (Path(r"C:/Windows/Fonts/NanumGothicBold.ttf"), _FONT_BOLD),
    ]
    reg_ok = bold_ok = False
    for path, name in candidates:
        if path.exists():
            try:
                pdfmetrics.registerFont(TTFont(name, str(path)))
                if name == _FONT_REG:
                    reg_ok = True
                if name == _FONT_BOLD:
                    bold_ok = True
            except Exception:  # noqa: BLE001
                pass
    if reg_ok and not bold_ok:
        pdfmetrics.registerFontFamily(_FONT_REG, normal=_FONT_REG, bold=_FONT_REG)
        bold_ok = True
    elif bold_ok and not reg_ok:
        pdfmetrics.registerFontFamily(_FONT_BOLD, normal=_FONT_BOLD, bold=_FONT_BOLD)
        reg_ok = True
    _FONTS_OK = reg_ok


def _usable_w() -> float:
    return PAGE_W - 2 * MARGIN


def _col_widths(ratios: list[float]) -> list[float]:
    total = sum(ratios)
    w = _usable_w()
    return [w * r / total for r in ratios]


def _styles() -> dict[str, ParagraphStyle]:
    _register_fonts()
    font = _FONT_REG if _FONTS_OK else "Helvetica"
    bold = _FONT_BOLD if _FONTS_OK else "Helvetica-Bold"
    return {
        "cover_title": ParagraphStyle(
            "cover_title",
            fontName=bold,
            fontSize=FS_COVER_TITLE,
            leading=FS_COVER_TITLE + 6,
            textColor=colors.HexColor("#1a1a1a"),
            alignment=TA_LEFT,
        ),
        "cover_sub": ParagraphStyle(
            "cover_sub",
            fontName=font,
            fontSize=FS_COVER_SUB,
            leading=FS_COVER_SUB + 4,
            textColor=colors.HexColor("#555555"),
            alignment=TA_LEFT,
        ),
        "slide_title": ParagraphStyle(
            "slide_title",
            fontName=bold,
            fontSize=FS_SLIDE_TITLE,
            leading=FS_SLIDE_TITLE + 4,
            textColor=colors.HexColor("#1a73e8"),
            alignment=TA_LEFT,
        ),
        "slide_sub": ParagraphStyle(
            "slide_sub",
            fontName=font,
            fontSize=FS_SLIDE_SUB,
            leading=FS_SLIDE_SUB + 3,
            textColor=colors.HexColor("#666666"),
            alignment=TA_LEFT,
        ),
        "cell_header": ParagraphStyle(
            "cell_header",
            fontName=bold,
            fontSize=FS_TABLE_HEADER,
            leading=FS_TABLE_LEADING,
            textColor=colors.white,
            alignment=TA_CENTER,
        ),
        "cell": ParagraphStyle(
            "cell",
            fontName=font,
            fontSize=FS_TABLE_BODY,
            leading=FS_TABLE_LEADING,
            alignment=TA_CENTER,
        ),
        "cell_left": ParagraphStyle(
            "cell_left",
            fontName=font,
            fontSize=FS_TABLE_BODY,
            leading=FS_TABLE_LEADING,
            alignment=TA_LEFT,
        ),
        "bullet": ParagraphStyle(
            "bullet",
            fontName=font,
            fontSize=FS_BULLET,
            leading=FS_BULLET + 4,
            alignment=TA_LEFT,
        ),
        "empty_note": ParagraphStyle(
            "empty_note",
            fontName=font,
            fontSize=FS_BULLET,
            leading=FS_BULLET + 6,
            textColor=colors.HexColor("#444444"),
            alignment=TA_LEFT,
        ),
        "kpi_label": ParagraphStyle(
            "kpi_label",
            fontName=font,
            fontSize=FS_KPI_LABEL,
            leading=FS_KPI_LABEL + 2,
            textColor=colors.HexColor("#666666"),
            alignment=TA_CENTER,
        ),
        "kpi_value": ParagraphStyle(
            "kpi_value",
            fontName=bold,
            fontSize=FS_KPI_VALUE,
            leading=FS_KPI_VALUE + 4,
            alignment=TA_CENTER,
        ),
    }


def _p(text: Any, style: ParagraphStyle) -> Paragraph:
    s = str(text or "").replace("&", "&amp;").replace("<", "&lt;")
    return Paragraph(s, style)


def _fmt_num(n: float | int) -> str:
    return f"{int(round(float(n))):,}"


def _table(
    data: list[list[Any]],
    col_widths: list[float] | None = None,
    *,
    left_cols: set[int] | None = None,
) -> Table:
    st = _styles()
    left_cols = left_cols or set()
    wrapped: list[list[Any]] = []
    for ri, row in enumerate(data):
        out_row: list[Any] = []
        for ci, cell in enumerate(row):
            if ri == 0:
                out_row.append(_p(cell, st["cell_header"]))
            elif ci in left_cols:
                out_row.append(_p(cell, st["cell_left"]))
            else:
                out_row.append(_p(cell, st["cell"]))
        wrapped.append(out_row)
    tbl = Table(wrapped, colWidths=col_widths, repeatRows=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a73e8")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#dde3ea")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]
    # 헤더 텍스트로 "해당 주"/"그 전 주" 컬럼을 자동 감지해 옅게 색을 입힌다 (배너와 같은 팔레트).
    if data:
        header = [str(c) for c in data[0]]
        for ci, label in enumerate(header):
            if "해당 주" in label:
                style_cmds.append(("BACKGROUND", (ci, 1), (ci, -1), colors.Color(26 / 255, 115 / 255, 232 / 255, alpha=0.10)))
            elif "그 전 주" in label:
                style_cmds.append(("BACKGROUND", (ci, 1), (ci, -1), colors.Color(217 / 255, 48 / 255, 37 / 255, alpha=0.08)))
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


def _append_paginated_table(
    story: list[Any],
    *,
    title: str,
    subtitle: str,
    data: list[list[Any]],
    col_ratios: list[float],
    rows_per_page: int = 10,
    left_cols: set[int] | None = None,
    cont_title: str | None = None,
) -> None:
    """표를 페이지 높이에 맞게 나눠 넣고, 이어지는 페이지에도 제목·헤더를 반복."""
    if len(data) <= 1:
        story.extend(_slide_header(title, subtitle))
        story.append(_table(data, _col_widths(col_ratios), left_cols=left_cols))
        return

    header = data[0]
    body = data[1:]
    col_w = _col_widths(col_ratios)
    cont = cont_title or title

    for page_i in range(0, len(body), rows_per_page):
        chunk = body[page_i : page_i + rows_per_page]
        if page_i == 0:
            page_title = title
        else:
            story.append(PageBreak())
            from_rank = page_i + 1
            to_rank = page_i + len(chunk)
            page_title = f"{cont} ({from_rank}–{to_rank}위)"

        story.extend(_slide_header(page_title, subtitle))
        story.append(_table([header, *chunk], col_w, left_cols=left_cols))


def _slide_header(title: str, subtitle: str) -> list[Any]:
    st = _styles()
    return [
        _p(title, st["slide_title"]),
        _p(subtitle, st["slide_sub"]),
        Spacer(1, 0.3 * cm),
    ]


def _draw_frame(canvas, doc) -> None:
    canvas.saveState()
    canvas.setFillColor(colors.HexColor("#1a73e8"))
    canvas.rect(0, PAGE_H - 0.6 * cm, PAGE_W, 0.6 * cm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont(_FONT_BOLD if _FONTS_OK else "Helvetica-Bold", FS_FRAME)
    canvas.drawString(MARGIN, PAGE_H - 0.42 * cm, "주간 광고 결과")
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.setFont(_FONT_REG if _FONTS_OK else "Helvetica", FS_FRAME - 1)
    canvas.drawRightString(PAGE_W - MARGIN, 0.4 * cm, f"{canvas.getPageNumber()}")
    canvas.restoreState()


def _week_pair(week_off: int) -> tuple[date, date, date, date]:
    _, _, this_mon, this_sun = time_utils.calendar_week_bounds(week_off)
    _, _, prev_mon, prev_sun = time_utils.calendar_week_bounds(week_off - 1)
    return this_mon, this_sun, prev_mon, prev_sun


def _integrated_kpis(week_off: int) -> dict[str, float]:
    _, _, this_mon, _ = time_utils.calendar_week_bounds(week_off)
    _, _, prev_mon, _ = time_utils.calendar_week_bounds(week_off - 1)
    lookback = max(42, int(config.SYNC_LOOKBACK_DAYS))
    merged_all = merge.load_merged_range(*time_utils.last_n_days_range(lookback))

    def _slice(off: int):
        w_start, w_end, _, _ = time_utils.calendar_week_bounds(off)
        if merged_all.empty:
            return merged_all
        return merged_all[(merged_all["ts_hour"] >= w_start) & (merged_all["ts_hour"] <= w_end)]

    wa = merge.week_ad_performance(_slice(week_off), week_start=this_mon.isoformat())
    wp = merge.week_ad_performance(_slice(week_off - 1), week_start=prev_mon.isoformat())
    return {"curr": wa, "prev": wp}


def _chart_image(png_bytes: bytes | None, *, height_cm: float = 12.5) -> Any:
    """PNG → 슬라이드 폭에 맞춘 Image flowable."""
    if not png_bytes:
        return Spacer(1, 0.2 * cm)
    img = Image(io.BytesIO(png_bytes))
    img.drawWidth = _usable_w()
    img.drawHeight = height_cm * cm
    img.hAlign = "CENTER"
    return img


def _chart_row(left: bytes | None, right: bytes | None, *, height_cm: float = 11.8) -> Table:
    """좌·우 차트를 한 슬라이드에 나란히."""
    half = _usable_w() / 2 - 0.15 * cm
    h = height_cm * cm
    cells: list[Any] = []
    for png in (left, right):
        if png:
            im = Image(io.BytesIO(png))
            im.drawWidth = half
            im.drawHeight = h
            cells.append(im)
        else:
            cells.append(Spacer(half, h))
    tbl = Table([cells], colWidths=[half + 0.15 * cm, half + 0.15 * cm])
    tbl.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("ALIGN", (0, 0), (-1, -1), "CENTER")]))
    return tbl


def _weekly_trend_rows(week_off: int, max_weeks: int = 8) -> list[dict[str, Any]]:
    lookback = max(42, int(config.SYNC_LOOKBACK_DAYS))
    merged_all = merge.load_merged_range(*time_utils.last_n_days_range(lookback))
    rows: list[dict[str, Any]] = []
    for off in range(week_off - max_weeks + 1, week_off + 1):
        w_start, w_end, d0, d1 = time_utils.calendar_week_bounds(off)
        if merged_all.empty:
            m = merged_all
        else:
            m = merged_all[(merged_all["ts_hour"] >= w_start) & (merged_all["ts_hour"] <= w_end)]
        wap = merge.week_ad_performance(m, week_start=d0.isoformat())
        rows.append(
            {
                "월~일": f"{d0} ~ {d1}",
                "통합 클릭": int(round(wap["total_clicks"])),
                "통합 비용(원)": round(wap["total_cost"], 0),
            }
        )
    return rows


def _kpi_slide(this_mon, this_sun, prev_mon, prev_sun, week_off: int) -> list[Any]:
    st = _styles()
    kpis = _integrated_kpis(week_off)
    c, p = kpis["curr"], kpis["prev"]
    cap = (
        f"해당 주 · {time_utils.format_week_range(this_mon, this_sun)}  |  "
        f"그 전 주 · {time_utils.format_week_range(prev_mon, prev_sun)}"
    )
    story: list[Any] = _slide_header("통합 광고 KPI", cap)

    lookback = max(42, int(config.SYNC_LOOKBACK_DAYS))
    merged_all = merge.load_merged_range(*time_utils.last_n_days_range(lookback))

    def _period(off: int):
        w_start, w_end, _, _ = time_utils.calendar_week_bounds(off)
        if merged_all.empty:
            return merged_all
        return merged_all[(merged_all["ts_hour"] >= w_start) & (merged_all["ts_hour"] <= w_end)]

    roas_c = merge.period_totals(_period(week_off))["roas"]
    roas_p = merge.period_totals(_period(week_off - 1))["roas"]

    labels = ["통합 클릭", "블렌드 CTR", "블렌드 CPC", "통합 광고비", "ROAS"]
    curr_vals = [
        _fmt_num(c["total_clicks"]),
        f"{c['blended_ctr_pct']:.2f}%",
        f"{_fmt_num(c['blended_cpc'])}원",
        f"{_fmt_num(c['total_cost'])}원",
        f"{roas_c:.2f}x",
    ]
    prev_vals = [
        _fmt_num(p["total_clicks"]),
        f"{p['blended_ctr_pct']:.2f}%",
        f"{_fmt_num(p['blended_cpc'])}원",
        f"{_fmt_num(p['total_cost'])}원",
        f"{roas_p:.2f}x",
    ]

    col_w = _usable_w() / 5
    rows = [[_p(labels[i], st["kpi_label"]) for i in range(5)]]
    rows.append([_p(curr_vals[i], st["kpi_value"]) for i in range(5)])
    rows.append([_p(f"전주 {prev_vals[i]}", st["slide_sub"]) for i in range(5)])
    tbl = Table(rows, colWidths=[col_w] * 5)
    tbl.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#dde3ea")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#eef2f7")),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8f0fe")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 14),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
            ]
        )
    )
    story.append(tbl)
    return story


def _append_homepage_section(
    story: list[Any],
    *,
    this_mon,
    this_sun,
    prev_mon,
    prev_sun,
    cap: str,
) -> None:
    """홈페이지 · GA4 — 세션·채널·주요 전환 이벤트."""
    st = _styles()
    p_tot = ga4_weekly_report.week_totals(prev_mon.isoformat())
    c_tot = ga4_weekly_report.week_totals(this_mon.isoformat())
    has_any = bool(c_tot.get("has_snapshot") or p_tot.get("has_snapshot"))

    story.extend(_slide_header("홈페이지 · GA4 주간 핵심 지표", cap))
    if not has_any:
        story.append(
            _p(
                "홈페이지 GA4 주간 스냅샷이 없습니다. 「홈페이지 동기화」 또는 GA4 재조회로 먼저 수집해 주세요.",
                st["empty_note"],
            )
        )
        return

    kpi_data = [
        ["지표", "해당 주", "그 전 주", "증감"],
        [
            "세션",
            _fmt_num(c_tot.get("sessions") or 0),
            _fmt_num(p_tot.get("sessions") or 0),
            f"{int((c_tot.get('sessions') or 0) - (p_tot.get('sessions') or 0)):+,}",
        ],
        [
            "활성 사용자",
            _fmt_num(c_tot.get("active_users") or 0),
            _fmt_num(p_tot.get("active_users") or 0),
            f"{int((c_tot.get('active_users') or 0) - (p_tot.get('active_users') or 0)):+,}",
        ],
    ]
    story.append(_table(kpi_data, _col_widths([34, 22, 22, 22]), left_cols={0}))
    story.append(PageBreak())

    ch = ga4_weekly_report.channel_weekly_compare(this_mon.isoformat(), prev_mon.isoformat())
    story.extend(_slide_header("홈페이지 · 채널별 유입 비교", cap + " · 세션 기준"))
    if ch.empty:
        story.append(_p("채널별 데이터가 없습니다.", st["empty_note"]))
    else:
        ch_data = [["채널", "세션(해당)", "세션(전주)", "증감", "활성 사용자(해당)"]]
        for _, r in ch.iterrows():
            ch_data.append(
                [
                    str(r["label"]),
                    _fmt_num(r["sessions_curr"]),
                    _fmt_num(r["sessions_prev"]),
                    f"{int(r['session_change']):+,}",
                    _fmt_num(r["active_users_curr"]),
                ]
            )
        _append_paginated_table(
            story,
            title="홈페이지 · 채널별 유입 비교",
            subtitle=cap + " · 세션 기준",
            data=ch_data,
            col_ratios=[32, 16, 16, 14, 22],
            rows_per_page=12,
            left_cols={0},
            cont_title="홈페이지 · 채널별 유입 비교",
        )
    story.append(PageBreak())

    ev = ga4_weekly_report.key_events_compare(this_mon.isoformat(), prev_mon.isoformat())
    story.extend(_slide_header("홈페이지 · 주요 전환 이벤트", cap))
    if ev.empty:
        story.append(_p("이벤트 데이터가 없습니다.", st["empty_note"]))
    else:
        ev_data = [["이벤트", "해당 주", "그 전 주", "증감"]]
        for _, r in ev.iterrows():
            ev_data.append(
                [
                    str(r["label"]),
                    _fmt_num(r["count_curr"]),
                    _fmt_num(r["count_prev"]),
                    f"{int(r['change']):+,}",
                ]
            )
        story.append(_table(ev_data, _col_widths([46, 18, 18, 18]), left_cols={0}))
        if int(ev["count_curr"].sum()) <= 0:
            story.append(Spacer(1, 0.3 * cm))
            story.append(
                _p(
                    "이 주 주요 전환 이벤트는 0건입니다. GTM 태그 게시·방문을 확인하세요.",
                    st["empty_note"],
                )
            )

    try:
        notes = ga4_weekly_report.build_homepage_analysis(prev_mon, prev_sun, this_mon, this_sun)
        if notes:
            story.append(PageBreak())
            story.extend(_slide_header("홈페이지 · 데이터 해석 · 전주 대비", cap))
            for line in notes[:10]:
                clean = str(line).replace("**", "").replace("`", "")
                story.append(_p(f"• {clean}", st["bullet"]))
                story.append(Spacer(1, 0.15 * cm))
    except Exception:  # noqa: BLE001
        pass


def _append_google_section(
    story: list[Any],
    *,
    this_mon,
    this_sun,
    prev_mon,
    prev_sun,
    cap: str,
) -> None:
    """구글 광고 · 주간 성과 — KPI + 캠페인별 비교 (대시보드 🔵 구글 섹션과 동일 구성)."""
    st = _styles()
    p_tot = google_weekly_report.week_totals(prev_mon.isoformat())
    c_tot = google_weekly_report.week_totals(this_mon.isoformat())
    has_any = bool(c_tot.get("has_snapshot") or p_tot.get("has_snapshot"))

    story.extend(_slide_header("구글 광고 · 주간 핵심 지표", cap))
    if not has_any:
        story.append(
            _p(
                "구글 주간 스냅샷이 없습니다. 「구글 동기화」 또는 재조회로 먼저 수집해 주세요.",
                st["empty_note"],
            )
        )
        return

    kpi_data = [
        ["지표", "해당 주", "그 전 주", "증감"],
        [
            "비용",
            f"{_fmt_num(c_tot.get('cost') or 0)}원",
            f"{_fmt_num(p_tot.get('cost') or 0)}원",
            f"{int((c_tot.get('cost') or 0) - (p_tot.get('cost') or 0)):+,}원",
        ],
        [
            "클릭",
            _fmt_num(c_tot.get("clicks") or 0),
            _fmt_num(p_tot.get("clicks") or 0),
            f"{int((c_tot.get('clicks') or 0) - (p_tot.get('clicks') or 0)):+,}",
        ],
        [
            "노출",
            _fmt_num(c_tot.get("impressions") or 0),
            _fmt_num(p_tot.get("impressions") or 0),
            f"{int((c_tot.get('impressions') or 0) - (p_tot.get('impressions') or 0)):+,}",
        ],
        [
            "전환",
            f"{float(c_tot.get('conversions') or 0):,.1f}",
            f"{float(p_tot.get('conversions') or 0):,.1f}",
            f"{float(c_tot.get('conversions') or 0) - float(p_tot.get('conversions') or 0):+,.1f}",
        ],
    ]
    story.append(_table(kpi_data, _col_widths([28, 24, 24, 24]), left_cols={0}))
    story.append(PageBreak())

    cmp_df = google_weekly_report.campaign_weekly_compare(this_mon.isoformat(), prev_mon.isoformat())
    camp_cap = cap + " · 비용 기준 정렬"
    if cmp_df.empty:
        story.extend(_slide_header("구글 · 캠페인별 주간 비교", camp_cap))
        story.append(_p("캠페인 데이터 없음", st["empty_note"]))
    else:
        camp_data = [
            [
                "순위",
                "캠페인",
                "비용(해당 주)",
                "비용(그 전 주)",
                "증감",
                "클릭(해당 주)",
                "클릭(그 전 주)",
                "증감",
            ]
        ]
        for _, r in cmp_df.iterrows():
            camp_data.append(
                [
                    int(r["rank"]),
                    str(r["campaign_name"]),
                    _fmt_num(r["cost_curr"]),
                    _fmt_num(r["cost_prev"]),
                    f"{int(r['cost_change']):+,}",
                    _fmt_num(r["clicks_curr"]),
                    _fmt_num(r["clicks_prev"]),
                    f"{int(r['click_change']):+,}",
                ]
            )
        _append_paginated_table(
            story,
            title="구글 · 캠페인별 주간 비교",
            subtitle=camp_cap,
            data=camp_data,
            col_ratios=[6, 30, 11, 11, 10, 11, 11, 10],
            rows_per_page=10,
            left_cols={1},
            cont_title="구글 · 캠페인별 주간 비교",
        )


def _ai_analysis_slides(ai_text: str | None, cap: str) -> list[Any]:
    """AI 주간 분석 슬라이드 — 내용 있으면 채우고, 없으면 안내."""
    st = _styles()
    story: list[Any] = [_p("🤖 AI 주간 분석", st["slide_title"]), _p(cap, st["slide_sub"]), Spacer(1, 0.25 * cm)]

    body = (ai_text or "").strip()
    if not body:
        story.append(
            _p(
                "대시보드 ③ AI 주간 분석에서 「총체적 주간 분석 시작」을 실행한 뒤 "
                "PDF를 다시 받으면 이 페이지에 분석 내용이 자동으로 포함됩니다.",
                st["empty_note"],
            )
        )
        story.append(Spacer(1, 0.35 * cm))
        story.append(_p("• 한 줄 요약 · 통합 성과 · 채널별 · 캠페인 · 키워드 · 추세 · 실행 제안", st["bullet"]))
        return story

    # 마크다운 간단 변환 — ## 제목, - 불릿, **굵게** 제거
    import re

    chunks: list[str] = []
    for line in body.splitlines():
        line = line.strip()
        if not line:
            continue
        line = re.sub(r"^#+\s*", "", line)
        line = line.replace("**", "")
        if line.startswith("- ") or line.startswith("* "):
            chunks.append(f"• {line[2:].strip()}")
        elif re.match(r"^\d+\.\s", line):
            chunks.append(f"• {line}")
        else:
            chunks.append(line)

    for para in chunks:
        story.append(_p(para, st["bullet"]))
        story.append(Spacer(1, 0.12 * cm))
    return story


def build_weekly_pdf(week_off: int = -1, *, ai_text: str | None = None) -> bytes:
    """
    가로형 PPT 스타일 PDF (bytes).
    week_off: 사이드바 조회 주와 동일 (0=이번 주, -1=막 끝난 주).
    """
    _register_fonts()
    this_mon, this_sun, prev_mon, prev_sun = _week_pair(week_off)
    st = _styles()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=(PAGE_W, PAGE_H),
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=MARGIN + 0.45 * cm,
        bottomMargin=MARGIN,
        title="주간 광고 결과",
    )
    story: list[Any] = []

    # ── 표지 ──
    story.append(Spacer(1, 2.2 * cm))
    story.append(_p("주간 광고 결과 보고서", st["cover_title"]))
    story.append(Spacer(1, 0.45 * cm))
    story.append(_p(f"해당 주 · {time_utils.format_week_range(this_mon, this_sun)}", st["cover_sub"]))
    story.append(_p(f"그 전 주 · {time_utils.format_week_range(prev_mon, prev_sun)}", st["cover_sub"]))
    story.append(Spacer(1, 0.25 * cm))
    story.append(_p(f"생성 · {config.TZ}", st["cover_sub"]))
    story.append(PageBreak())

    # ── 통합 KPI ──
    story.extend(_kpi_slide(this_mon, this_sun, prev_mon, prev_sun, week_off))
    story.append(PageBreak())

    cap = _week_compare_caption_text(this_mon, this_sun, prev_mon, prev_sun)

    # ══════════════ 네이버 — 대시보드와 같은 순서로 한 블록에 몰아서 ══════════════
    p_tot = naver_weekly_report.week_totals(prev_mon.isoformat())
    c_tot = naver_weekly_report.week_totals(this_mon.isoformat())
    prev_camp = naver_weekly_report.campaigns_df(prev_mon.isoformat())
    curr_camp = naver_weekly_report.campaigns_df(this_mon.isoformat())

    # ── 네이버 · 주간 총계 ──
    summary = naver_weekly_report.build_summary_table(prev_mon, prev_sun, this_mon, this_sun)
    sum_data = [[summary.columns[0], "해당 주", "그 전 주", "증감"]]
    for _, row in summary.iterrows():
        sum_data.append([row.iloc[0], row.iloc[2], row.iloc[1], row.iloc[3]])
    _append_paginated_table(
        story,
        title="네이버 검색광고 — 주간 총계",
        subtitle=cap,
        data=sum_data,
        col_ratios=[38, 22, 22, 18],
        rows_per_page=12,
        left_cols={0},
    )
    story.append(PageBreak())

    # ── 네이버 · 시각 요약: 캠페인 비용 원형(주간 비교) ──
    pie_pair_png = charts.campaign_cost_pie_pair_png(
        prev_camp,
        curr_camp,
        prev_title=f"그 전 주 · {prev_mon.month:02d}/{prev_mon.day:02d}~{prev_sun.month:02d}/{prev_sun.day:02d}",
        curr_title=f"해당 주 · {this_mon.month:02d}/{this_mon.day:02d}~{this_sun.month:02d}/{this_sun.day:02d}",
    )
    story.extend(_slide_header("네이버 · 캠페인별 비용 비중", cap))
    if pie_pair_png:
        story.append(_chart_image(pie_pair_png, height_cm=13.5))
    else:
        story.append(_p("캠페인 비용 데이터 없음", st["empty_note"]))
    story.append(PageBreak())

    # ── 네이버 · 핵심 지표 막대 (지표별 독립 스케일) ──
    bar_png = charts.naver_week_compare_bar_png(p_tot, c_tot)
    story.extend(_slide_header("네이버 · 주간 핵심 지표 비교", cap))
    story.append(_chart_image(bar_png, height_cm=13.0))
    story.append(PageBreak())

    # ── 네이버 · 캠페인별 주간 비교 ──
    camp = naver_weekly_report.campaign_weekly_compare(this_mon.isoformat(), prev_mon.isoformat())
    camp_cap = cap + " · 비용 기준 정렬"
    if camp.empty:
        story.extend(_slide_header("네이버 · 캠페인별 주간 비교", camp_cap))
        story.append(_p("캠페인 데이터 없음", st["empty_note"]))
    else:
        camp_data = [
            [
                "순위",
                "캠페인",
                "비용(해당 주)",
                "비용(그 전 주)",
                "증감",
                "클릭(해당 주)",
                "클릭(그 전 주)",
                "증감",
            ]
        ]
        for _, r in camp.iterrows():
            camp_data.append(
                [
                    int(r["rank"]),
                    str(r["campaign_name"]),
                    _fmt_num(r["cost_curr"]),
                    _fmt_num(r["cost_prev"]),
                    f"{int(r['cost_change']):+,}",
                    _fmt_num(r["clicks_curr"]),
                    _fmt_num(r["clicks_prev"]),
                    f"{int(r['click_change']):+,}",
                ]
            )
        _append_paginated_table(
            story,
            title="네이버 · 캠페인별 주간 비교",
            subtitle=camp_cap,
            data=camp_data,
            col_ratios=[6, 30, 11, 11, 10, 11, 11, 10],
            rows_per_page=10,
            left_cols={1},
            cont_title="네이버 · 캠페인별 주간 비교",
        )
    story.append(PageBreak())

    # ── 네이버 · 키워드 TOP 20 ──
    kw = naver_weekly_report.keyword_clicks_compare(this_mon.isoformat(), prev_mon.isoformat(), top_n=20)
    kw_cap = cap + " · 해당 주 클릭 기준"
    if kw.empty:
        story.extend(_slide_header("네이버 · 키워드 TOP 20 · 클릭 비교", kw_cap))
        story.append(
            _p(
                "키워드 데이터가 없습니다. 「전체 동기화」 후 완료된 주(지난 주)를 선택해 주세요.",
                st["empty_note"],
            )
        )
    else:
        kw_data = [["순위", "키워드", "클릭(해당 주)", "클릭(그 전 주)", "증감", "증감률", "캠페인"]]
        for _, r in kw.iterrows():
            pct = r.get("click_change_pct")
            pct_s = f"{pct:+.1f}%" if pct is not None and pct == pct else "–"
            kw_data.append(
                [
                    int(r["rank"]),
                    str(r["keyword"]),
                    _fmt_num(r["clicks_curr"]),
                    _fmt_num(r["clicks_prev"]),
                    f"{int(r['click_change']):+,}",
                    pct_s,
                    str(r.get("campaign_name") or ""),
                ]
            )
        _append_paginated_table(
            story,
            title="네이버 · 키워드 TOP 20 · 클릭 비교",
            subtitle=kw_cap,
            data=kw_data,
            col_ratios=[6, 24, 12, 12, 11, 11, 24],
            rows_per_page=12,
            left_cols={1, 6},
            cont_title="네이버 · 키워드 TOP 20 · 클릭 비교",
        )
    story.append(PageBreak())

    # ── 네이버 · 통합 광고 주간 추이 (네이버+구글 합산 클릭·비용 추세) ──
    trend_rows = _weekly_trend_rows(week_off, max_weeks=8)
    trend_png = charts.integrated_week_trend_png(
        trend_rows,
        title="통합 광고 · 최근 주간 추이",
    )
    if trend_png:
        story.extend(_slide_header("통합 광고 · 주간 추이", "클릭(상) · 비용(하) — 각각 독립 Y축 · 월~일 완료 주"))
        story.append(_chart_image(trend_png, height_cm=13.5))
        story.append(PageBreak())

    # ── 네이버 · 캠페인 분석 코멘트 ──
    try:
        notes = naver_weekly_report.build_campaign_analysis(prev_mon, prev_sun, this_mon, this_sun)
        if notes:
            story.extend(_slide_header("네이버 · 캠페인 분석 요약", cap))
            for line in notes[:6]:
                clean = str(line).replace("**", "")
                story.append(_p(f"• {clean}", st["bullet"]))
                story.append(Spacer(1, 0.2 * cm))
            story.append(PageBreak())
    except Exception:  # noqa: BLE001
        pass

    # ══════════════ 구글 광고 ══════════════
    _append_google_section(
        story,
        this_mon=this_mon,
        this_sun=this_sun,
        prev_mon=prev_mon,
        prev_sun=prev_sun,
        cap=cap,
    )
    story.append(PageBreak())

    # ══════════════ 홈페이지 · GA4 ══════════════
    _append_homepage_section(
        story,
        this_mon=this_mon,
        this_sun=this_sun,
        prev_mon=prev_mon,
        prev_sun=prev_sun,
        cap=cap,
    )
    story.append(PageBreak())

    # ══════════════ AI 주간 분석 ══════════════
    story.extend(_ai_analysis_slides(ai_text, cap))

    doc.build(story, onFirstPage=_draw_frame, onLaterPages=_draw_frame)
    return buf.getvalue()


def _week_compare_caption_text(this_mon, this_sun, prev_mon, prev_sun) -> str:
    return (
        f"해당 주 · {time_utils.format_week_range(this_mon, this_sun)}  |  "
        f"그 전 주 · {time_utils.format_week_range(prev_mon, prev_sun)}"
    )
