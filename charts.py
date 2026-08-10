"""대시보드(Plotly) · PDF(matplotlib) 공용 차트."""
from __future__ import annotations

import io
from typing import Any

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

PALETTE = [
    "#1a73e8",
    "#d93025",
    "#f9ab00",
    "#0d652d",
    "#9334e6",
    "#e37400",
    "#12b5cb",
    "#64748b",
    "#c5221f",
    "#188038",
]

# Plotly: use_container_width 기준 — 고정 height만 보조
CHART_HEIGHT_LG = 520
CHART_HEIGHT_MD = 480


def _short_name(name: str, max_len: int = 20) -> str:
    s = str(name or "(없음)")
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


def _week_short_label(mon, sun) -> str:
    return f"{mon.month:02d}/{mon.day:02d}~{sun.month:02d}/{sun.day:02d}"


def _short_week_col(label: str) -> str:
    """'2025-06-16 ~ 2025-06-22' → '06/16~06/22'."""
    s = str(label or "")
    if " ~ " not in s:
        return s
    a, b = s.split(" ~ ", 1)
    try:
        from datetime import date

        d0 = date.fromisoformat(a.strip())
        d1 = date.fromisoformat(b.strip())
        return _week_short_label(d0, d1)
    except ValueError:
        return s


def _campaign_color_map(names: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, n in enumerate(names):
        out[n] = PALETTE[i % len(PALETTE)]
    return out


def campaign_cost_pie_data(campaign_df: pd.DataFrame, *, top_n: int = 8) -> pd.DataFrame:
    """비용 > 0 캠페인 — 상위 N-1 + 기타."""
    if campaign_df.empty or "cost" not in campaign_df.columns:
        return pd.DataFrame()
    df = campaign_df.copy()
    df["cost"] = pd.to_numeric(df["cost"], errors="coerce").fillna(0.0)
    df = df[df["cost"] > 0].sort_values("cost", ascending=False)
    if df.empty:
        return pd.DataFrame()
    if len(df) > top_n:
        head = df.head(top_n - 1).copy()
        rest = float(df.iloc[top_n - 1 :]["cost"].sum())
        if rest > 0:
            extra = pd.DataFrame(
                [{"campaign_name": f"기타 ({len(df) - top_n + 1}개)", "cost": rest}]
            )
            df = pd.concat([head, extra], ignore_index=True)
        else:
            df = head
    total = float(df["cost"].sum())
    df["share_pct"] = df["cost"].apply(lambda x: 100.0 * float(x) / total if total > 0 else 0.0)
    return df.reset_index(drop=True)


def _pie_figure_from_data(
    data: pd.DataFrame,
    *,
    title: str,
    color_map: dict[str, str] | None = None,
) -> go.Figure | None:
    if data.empty:
        return None
    labels = [_short_name(n) for n in data["campaign_name"]]
    colors = []
    for raw in data["campaign_name"]:
        key = _short_name(raw)
        if color_map and str(raw) in color_map:
            colors.append(color_map[str(raw)])
        elif color_map and key in color_map:
            colors.append(color_map[key])
        else:
            colors.append(PALETTE[len(colors) % len(PALETTE)])
    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=data["cost"],
                hole=0.4,
                textinfo="label+percent",
                textposition="outside",
                textfont=dict(size=13),
                marker=dict(colors=colors, line=dict(color="#fff", width=1.5)),
                hovertemplate="%{label}<br>비용 %{value:,.0f}원<br>%{percent}<extra></extra>",
            )
        ]
    )
    fig.update_layout(
        title=dict(text=title, font=dict(size=16)),
        showlegend=False,
        margin=dict(l=16, r=16, t=48, b=16),
        uniformtext_minsize=10,
        uniformtext_mode="hide",
    )
    return fig


def campaign_cost_pie_plotly(
    campaign_df: pd.DataFrame,
    *,
    title: str = "캠페인별 비용 비중",
    color_map: dict[str, str] | None = None,
) -> go.Figure | None:
    data = campaign_cost_pie_data(campaign_df)
    fig = _pie_figure_from_data(data, title=title, color_map=color_map)
    if fig is not None:
        fig.update_layout(height=CHART_HEIGHT_MD)
    return fig


def campaign_cost_pie_pair_plotly(
    prev_df: pd.DataFrame,
    curr_df: pd.DataFrame,
    *,
    prev_title: str = "그 전 주",
    curr_title: str = "해당 주",
) -> go.Figure | None:
    """지난주·이번주 캠페인 비용 비중 — 색상 통일."""
    prev_data = campaign_cost_pie_data(prev_df)
    curr_data = campaign_cost_pie_data(curr_df)
    if prev_data.empty and curr_data.empty:
        return None

    all_names: list[str] = []
    for df in (prev_data, curr_data):
        for n in df.get("campaign_name", []):
            if str(n) not in all_names:
                all_names.append(str(n))
    color_map = _campaign_color_map(all_names)

    fig = make_subplots(
        rows=1,
        cols=2,
        specs=[[{"type": "domain"}, {"type": "domain"}]],
        subplot_titles=(prev_title, curr_title),
    )
    has_any = False
    for col, data, ttl in ((1, prev_data, prev_title), (2, curr_data, curr_title)):
        if data.empty:
            continue
        has_any = True
        labels = [_short_name(n) for n in data["campaign_name"]]
        colors = [color_map.get(str(n), PALETTE[i % len(PALETTE)]) for i, n in enumerate(data["campaign_name"])]
        fig.add_trace(
            go.Pie(
                labels=labels,
                values=data["cost"],
                hole=0.4,
                textinfo="label+percent",
                textposition="outside",
                textfont=dict(size=12),
                marker=dict(colors=colors, line=dict(color="#fff", width=1)),
                hovertemplate="%{label}<br>비용 %{value:,.0f}원<br>%{percent}<extra></extra>",
                name=ttl,
            ),
            row=1,
            col=col,
        )
    if not has_any:
        return None
    fig.update_layout(
        title=dict(text="캠페인별 비용 비중 · 주간 비교", font=dict(size=18)),
        height=CHART_HEIGHT_LG,
        margin=dict(l=20, r=20, t=72, b=20),
        showlegend=False,
    )
    fig.update_annotations(font_size=14)
    return fig


def naver_week_compare_bar_plotly(
    prev: dict[str, float],
    curr: dict[str, float],
    *,
    prev_label: str = "그 전 주",
    curr_label: str = "해당 주",
    title: str = "네이버 주간 핵심 지표 비교",
) -> go.Figure:
    """비용·클릭·CPC 각각 독립 Y축 — 스케일이 달라도 막대 비교 가능."""
    panels: list[tuple[str, float, float, str]] = [
        ("총 비용 (원)", float(prev.get("cost") or 0), float(curr.get("cost") or 0), ",.0f"),
        ("총 클릭", float(prev.get("clicks") or 0), float(curr.get("clicks") or 0), ",.0f"),
        ("평균 CPC (원)", float(prev.get("cpc") or 0), float(curr.get("cpc") or 0), ",.0f"),
    ]

    fig = make_subplots(
        rows=1,
        cols=3,
        subplot_titles=[p[0] for p in panels],
        horizontal_spacing=0.08,
    )

    for i, (label, pv, cv, fmt) in enumerate(panels, start=1):
        ymax = max(pv, cv, 1.0) * 1.22
        fig.add_trace(
            go.Bar(
                x=[prev_label, curr_label],
                y=[pv, cv],
                marker_color=["#94a3b8", "#1a73e8"],
                text=[f"{pv:{fmt}}", f"{cv:{fmt}}"],
                textposition="outside",
                textfont=dict(size=14),
                showlegend=False,
                hovertemplate="%{x}<br>%{y:,.0f}<extra></extra>",
            ),
            row=1,
            col=i,
        )
        fig.update_yaxes(range=[0, ymax], tickformat=fmt, row=1, col=i)

    fig.update_layout(
        title=dict(text=title, font=dict(size=18)),
        height=CHART_HEIGHT_LG,
        margin=dict(l=40, r=24, t=80, b=40),
        barmode="group",
    )
    fig.update_annotations(font_size=13)
    return fig


def integrated_week_trend_plotly(
    rows: list[dict[str, Any]],
    *,
    title: str = "통합 광고 · 주간 추이",
) -> go.Figure | None:
    """클릭(상) · 비용(하) 분리 패널 — 스케일 충돌 없음."""
    if not rows:
        return None
    df = pd.DataFrame(rows)
    if df.empty or "월~일" not in df.columns:
        return None

    x = [_short_week_col(v) for v in df["월~일"]]
    clicks = pd.to_numeric(df.get("통합 클릭", 0), errors="coerce").fillna(0)
    costs = pd.to_numeric(df.get("통합 비용(원)", 0), errors="coerce").fillna(0)

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
        subplot_titles=("주간 통합 클릭", "주간 통합 광고비 (원)"),
    )
    fig.add_trace(
        go.Bar(
            x=x,
            y=clicks,
            name="통합 클릭",
            marker_color="#1a73e8",
            text=[f"{int(v):,}" for v in clicks],
            textposition="outside",
            textfont=dict(size=12),
        ),
        row=1,
        col=1,
    )
    fig.add_trace(
        go.Bar(
            x=x,
            y=costs,
            name="통합 비용",
            marker_color="#d93025",
            text=[f"{int(v):,}" for v in costs],
            textposition="outside",
            textfont=dict(size=12),
        ),
        row=2,
        col=1,
    )
    fig.update_layout(
        title=dict(text=title, font=dict(size=18)),
        height=CHART_HEIGHT_LG + 80,
        showlegend=False,
        margin=dict(l=48, r=24, t=72, b=56),
    )
    fig.update_xaxes(tickangle=-28, tickfont=dict(size=11))
    fig.update_yaxes(tickformat=",.0f", row=1, col=1)
    fig.update_yaxes(tickformat=",.0f", row=2, col=1)
    fig.update_annotations(font_size=13)
    return fig


# ── matplotlib (PDF) ──


def _setup_matplotlib_korean() -> None:
    import matplotlib.pyplot as plt

    for fam in ("Malgun Gothic", "NanumGothic", "AppleGothic", "DejaVu Sans"):
        try:
            plt.rcParams["font.family"] = fam
            plt.rcParams["axes.unicode_minus"] = False
            return
        except Exception:  # noqa: BLE001
            continue
    plt.rcParams["axes.unicode_minus"] = False


def campaign_cost_pie_pair_png(
    prev_df: pd.DataFrame,
    curr_df: pd.DataFrame,
    *,
    prev_title: str = "그 전 주",
    curr_title: str = "해당 주",
    width_px: int = 1600,
    height_px: int = 820,
    dpi: int = 120,
) -> bytes | None:
    prev_data = campaign_cost_pie_data(prev_df)
    curr_data = campaign_cost_pie_data(curr_df)
    if prev_data.empty and curr_data.empty:
        return None

    import matplotlib.pyplot as plt

    _setup_matplotlib_korean()
    all_names: list[str] = []
    for df in (prev_data, curr_data):
        for n in df.get("campaign_name", []):
            if str(n) not in all_names:
                all_names.append(str(n))
    color_map = _campaign_color_map(all_names)

    fig_w, fig_h = width_px / dpi, height_px / dpi
    fig, axes = plt.subplots(1, 2, figsize=(fig_w, fig_h), dpi=dpi)

    for ax, data, ttl in (
        (axes[0], prev_data, prev_title),
        (axes[1], curr_data, curr_title),
    ):
        if data.empty:
            ax.text(0.5, 0.5, "데이터 없음", ha="center", va="center", fontsize=16)
            ax.set_title(ttl, fontsize=18, fontweight="bold")
            ax.axis("off")
            continue
        labels = [_short_name(n, 18) for n in data["campaign_name"]]
        colors = [color_map.get(str(n), PALETTE[i % len(PALETTE)]) for i, n in enumerate(data["campaign_name"])]
        wedges, texts, autotexts = ax.pie(
            data["cost"],
            labels=labels,
            autopct=lambda p: f"{p:.1f}%" if p >= 3 else "",
            startangle=90,
            colors=colors,
            pctdistance=0.78,
            textprops={"fontsize": 12},
        )
        for t in autotexts:
            t.set_fontsize(12)
            t.set_fontweight("bold")
        ax.set_title(ttl, fontsize=18, fontweight="bold", pad=12)

    fig.suptitle("캠페인별 비용 비중 · 주간 비교", fontsize=22, fontweight="bold", y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def naver_week_compare_bar_png(
    prev: dict[str, float],
    curr: dict[str, float],
    *,
    prev_label: str = "그 전 주",
    curr_label: str = "해당 주",
    title: str = "네이버 주간 핵심 지표 비교",
    width_px: int = 1600,
    height_px: int = 820,
    dpi: int = 120,
) -> bytes:
    import matplotlib.pyplot as plt
    import numpy as np

    _setup_matplotlib_korean()
    panels = [
        ("총 비용 (원)", float(prev.get("cost") or 0), float(curr.get("cost") or 0)),
        ("총 클릭", float(prev.get("clicks") or 0), float(curr.get("clicks") or 0)),
        ("평균 CPC (원)", float(prev.get("cpc") or 0), float(curr.get("cpc") or 0)),
    ]

    fig_w, fig_h = width_px / dpi, height_px / dpi
    fig, axes = plt.subplots(1, 3, figsize=(fig_w, fig_h), dpi=dpi)
    x = np.array([0, 1])
    w = 0.55

    for ax, (label, pv, cv) in zip(axes, panels):
        bars = ax.bar(x, [pv, cv], w, color=["#94a3b8", "#1a73e8"])
        ax.set_xticks(x)
        ax.set_xticklabels([prev_label, curr_label], fontsize=13)
        ax.set_title(label, fontsize=16, fontweight="bold")
        ymax = max(pv, cv, 1.0) * 1.25
        ax.set_ylim(0, ymax)
        for bar in bars:
            h = bar.get_height()
            ax.annotate(
                f"{h:,.0f}",
                xy=(bar.get_x() + bar.get_width() / 2, h),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=14,
                fontweight="bold",
            )
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.grid(axis="y", alpha=0.25)

    fig.suptitle(title, fontsize=22, fontweight="bold", y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def integrated_week_trend_png(
    rows: list[dict[str, Any]],
    *,
    title: str = "통합 광고 · 주간 추이",
    width_px: int = 1600,
    height_px: int = 900,
    dpi: int = 120,
) -> bytes | None:
    if not rows:
        return None
    import matplotlib.pyplot as plt
    import numpy as np

    _setup_matplotlib_korean()
    df = pd.DataFrame(rows)
    if df.empty:
        return None

    labels = [_short_week_col(v) for v in df["월~일"]]
    clicks = pd.to_numeric(df.get("통합 클릭", 0), errors="coerce").fillna(0).astype(float).tolist()
    costs = pd.to_numeric(df.get("통합 비용(원)", 0), errors="coerce").fillna(0).astype(float).tolist()
    x = np.arange(len(labels))

    fig_w, fig_h = width_px / dpi, height_px / dpi
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(fig_w, fig_h), dpi=dpi, sharex=True)

    for ax, vals, color, ylab in (
        (ax1, clicks, "#1a73e8", "통합 클릭"),
        (ax2, costs, "#d93025", "통합 광고비 (원)"),
    ):
        bars = ax.bar(x, vals, color=color, width=0.65)
        ax.set_ylabel(ylab, fontsize=14)
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v:,.0f}"))
        ax.grid(axis="y", alpha=0.2)
        for bar in bars:
            h = bar.get_height()
            if h <= 0:
                continue
            ax.annotate(
                f"{h:,.0f}",
                xy=(bar.get_x() + bar.get_width() / 2, h),
                xytext=(0, 4),
                textcoords="offset points",
                ha="center",
                va="bottom",
                fontsize=11,
                fontweight="bold",
            )

    ax2.set_xticks(x)
    ax2.set_xticklabels(labels, rotation=28, ha="right", fontsize=11)
    fig.suptitle(title, fontsize=22, fontweight="bold", y=0.98)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


# 하위 호환 alias
integrated_week_bar_plotly = integrated_week_trend_plotly
integrated_week_bar_png = integrated_week_trend_png


def campaign_cost_pie_png(
    campaign_df: pd.DataFrame,
    *,
    title: str = "캠페인별 비용 비중",
    width_px: int = 1280,
    height_px: int = 720,
    dpi: int = 120,
) -> bytes | None:
    """단일 주 원형 (레거시)."""
    data = campaign_cost_pie_data(campaign_df)
    if data.empty:
        return None
    import matplotlib.pyplot as plt

    _setup_matplotlib_korean()
    fig_w, fig_h = width_px / dpi, height_px / dpi
    fig, ax = plt.subplots(figsize=(fig_w, fig_h), dpi=dpi)
    labels = [_short_name(n, 22) for n in data["campaign_name"]]
    ax.pie(
        data["cost"],
        labels=labels,
        autopct=lambda p: f"{p:.1f}%" if p >= 3 else "",
        startangle=90,
        colors=PALETTE * 3,
        textprops={"fontsize": 13},
    )
    ax.set_title(title, fontsize=22, fontweight="bold", pad=16)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return buf.getvalue()
