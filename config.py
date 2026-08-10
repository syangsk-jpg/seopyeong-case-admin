"""Load settings from environment (.env supported via python-dotenv)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parent / ".env"
# utf-8-sig: Windows 硫붾え???????BOM ?뚮Ц???ㅺ? 源⑥???寃쎌슦 諛⑹?
load_dotenv(_env_path, encoding="utf-8-sig")
# ?ㅽ뻾 ?꾩튂媛 ?ㅻⅨ ?대뜑???뚮? ?鍮꾪빐 CWD??.env???쒕룄 (泥?濡쒕뱶媛 ?곗꽑)
load_dotenv(Path.cwd() / ".env", encoding="utf-8-sig", override=False)


def _get(key: str, default: str | None = None) -> str | None:
    v = os.environ.get(key, default)
    return v if v not in ("", None) else default


def _auth_value(key: str, default: str) -> str:
    """濡쒓렇?몄슜: 怨듬갚쨌CR ?쒓굅 ??鍮꾧탳."""
    raw = _get(key, default) or default
    return str(raw).strip().replace("\r", "")


DATABASE_PATH = Path(
    _get("DATABASE_PATH", str(Path(__file__).resolve().parent / "data" / "metrics.db"))
)
TZ = _get("TZ", "Asia/Seoul") or "Asia/Seoul"

# ??濡쒓렇??(珥덇린 import ????踰? 濡쒓렇??寃利앹? get_dashboard_credentials() ?ъ슜 沅뚯옣)
DASHBOARD_USER = _auth_value("DASHBOARD_USER", "admin")
DASHBOARD_PASSWORD = _auth_value("DASHBOARD_PASSWORD", "1234")


def get_dashboard_credentials() -> tuple[str, str]:
    """
    留??몄텧留덈떎 ?꾨줈?앺듃 猷⑦듃 `.env`瑜??ㅼ떆 ?쎌뒿?덈떎(Streamlit import 罹먯떆? 臾닿?).
    """
    load_dotenv(_env_path, encoding="utf-8-sig", override=True)
    u = _auth_value("DASHBOARD_USER", "admin")
    p = _auth_value("DASHBOARD_PASSWORD", "1234")
    return u, p

TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = _get("TELEGRAM_CHAT_ID")

NAVER_CUSTOMER_ID = _get("NAVER_CUSTOMER_ID")
NAVER_API_KEY = _get("NAVER_API_KEY")
NAVER_API_SECRET = _get("NAVER_API_SECRET")
NAVER_REFRESH_TOKEN = _get("NAVER_REFRESH_TOKEN")

GOOGLE_ADS_DEVELOPER_TOKEN = _get("GOOGLE_ADS_DEVELOPER_TOKEN")
GOOGLE_ADS_CLIENT_ID = _get("GOOGLE_ADS_CLIENT_ID")
GOOGLE_ADS_CLIENT_SECRET = _get("GOOGLE_ADS_CLIENT_SECRET")
GOOGLE_ADS_REFRESH_TOKEN = _get("GOOGLE_ADS_REFRESH_TOKEN")
GOOGLE_ADS_LOGIN_CUSTOMER_ID = _get("GOOGLE_ADS_LOGIN_CUSTOMER_ID")
GOOGLE_ADS_CUSTOMER_ID = _get("GOOGLE_ADS_CUSTOMER_ID")

GA4_PROPERTY_ID = _get("GA4_PROPERTY_ID")
GOOGLE_APPLICATION_CREDENTIALS = _get("GOOGLE_APPLICATION_CREDENTIALS")

# GA4 유입 채널·이벤트 주간 — 지난 N주 (월~일, Asia/Seoul)
GA4_WEEKLY_BACK_WEEKS = int(_get("GA4_WEEKLY_BACK_WEEKS", "5") or "5")

# Google Ads weekly 노출 분석 — 지난 N주 + 키워드 상위 M (월~일, Asia/Seoul)
GOOGLE_WEEKLY_BACK_WEEKS = int(_get("GOOGLE_WEEKLY_BACK_WEEKS", "5") or "5")
GOOGLE_WEEK_TOP_KEYWORDS = int(_get("GOOGLE_WEEK_TOP_KEYWORDS", "50") or "50")

# Optional: CSV with columns ts_hour, active_users, sessions [, bounce_rate]
TRAFFIC_LOG_CSV = _get("TRAFFIC_LOG_CSV")

USE_MOCK_DATA = (_get("USE_MOCK_DATA", "false") or "false").lower() in ("1", "true", "yes")

# Alert: spend spike vs baseline multiplier
SPEND_SPIKE_RATIO = float(_get("SPEND_SPIKE_RATIO", "1.5") or "1.5")

# How many days of history sync + dashboard load (weekly view needs past weeks in DB)
SYNC_LOOKBACK_DAYS = int(_get("SYNC_LOOKBACK_DAYS", "90") or "90")
# Regular sync: only re-fetch this many recent days (past data stays in SQLite).
# Full lookback (SYNC_LOOKBACK_DAYS) runs only when force=True or DB is empty.
SYNC_INCREMENTAL_DAYS = int(_get("SYNC_INCREMENTAL_DAYS", "2") or "2")

# Naver weekly 노출 분석 — 지난 N주 + 키워드 상위 M (월~일, Asia/Seoul)
NAVER_WEEKLY_BACK_WEEKS = int(_get("NAVER_WEEKLY_BACK_WEEKS", "5") or "5")
NAVER_WEEK_TOP_KEYWORDS = int(_get("NAVER_WEEK_TOP_KEYWORDS", "50") or "50")
NAVER_STATS_IDS_PER_REQUEST = int(_get("NAVER_STATS_IDS_PER_REQUEST", "300") or "300")

# Google Gemini — 주간 AI 브리핑 / 에이전트
GEMINI_API_KEY = _get("GEMINI_API_KEY")
# 기본 모델 (gemini-2.0-flash는 무료 할당량 소진이 잦아 2.5-flash 권장)
GEMINI_MODEL = _get("GEMINI_MODEL", "gemini-2.5-flash") or "gemini-2.5-flash"

# False면 로그인 화면 없이 바로 대시보드 진입
LOGIN_REQUIRED = (_get('LOGIN_REQUIRED', 'false') or 'false').lower() in ('1', 'true', 'yes')

# 구글 광고 AI 전략 제안 — 실제 계정 변경(쓰기) 허용 여부. 기본 false = 검토만 가능.
GOOGLE_ADS_ALLOW_WRITE = (_get("GOOGLE_ADS_ALLOW_WRITE", "false") or "false").lower() in ("1", "true", "yes")
# 예산/입찰가 변경 시 한 번에 허용하는 최대 변동폭(%) — 라이브 재조회 값 기준 하드 클램프
GOOGLE_ADS_MAX_BUDGET_CHANGE_PCT = float(_get("GOOGLE_ADS_MAX_BUDGET_CHANGE_PCT", "30") or "30")
GOOGLE_ADS_MAX_BID_CHANGE_PCT = float(_get("GOOGLE_ADS_MAX_BID_CHANGE_PCT", "30") or "30")
# 한 번의 AI 분석에서 생성할 최대 제안 개수
GOOGLE_AI_PROPOSAL_MAX_PER_RUN = int(_get("GOOGLE_AI_PROPOSAL_MAX_PER_RUN", "10") or "10")

