"""Gemini-based structuring for the existing Rhymix winning-case fields."""
from __future__ import annotations

import json
import re
import base64
from pathlib import Path

import gemini_weekly_analysis as gemini


SITE = Path(__file__).resolve().parent
WEBSITE_SNAPSHOT = (
    Path(r"H:\coding\splawilsan_homepage")
    / "_snapshots"
    / "ai_counsel_2026-07-23_baseline"
    / "_data"
    / "winning_cases.json"
)
LOCAL_SNAPSHOT = SITE / "data" / "winning_cases_reference.json"

CATEGORIES = [
    "민사·기업·손해배상",
    "음주·성범죄",
    "형사·폭행·명예훼손·사기·마약",
    "행정(소송·심판)",
    "상속·종중",
    "이혼·가사",
]

PATTERNS = [
    ("전화번호", r"(?<!\d)(?:01[016789]|0\d{1,2})[- .]?\d{3,4}[- .]?\d{4}(?!\d)"),
    ("이메일", r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    ("주민등록번호", r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)"),
    ("계좌·카드번호", r"(?<!\d)\d{2,6}[- ]\d{2,6}[- ]\d{2,6}(?:[- ]\d{1,6})?(?!\d)"),
    ("사건번호", r"\b(?:19|20)\d{2}\s*[가-힣]{1,5}\s*\d{2,10}\b"),
]


def _load_cases() -> list[dict]:
    for path in (LOCAL_SNAPSHOT, WEBSITE_SNAPSHOT):
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(rows, list) and rows:
                return rows
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
    return []


def style_profile(limit: int = 20) -> dict:
    rows = _load_cases()[:limit]
    if not rows:
        return {
            "sample_count": 0,
            "sections": ["서평을 찾게 된 경위", "변호사의 조력", "소송 결과", "사건의 의의"],
            "example_titles": [],
            "example_tags": [],
        }
    lengths = sorted(len(str(row.get("body", ""))) for row in rows)
    return {
        "sample_count": len(rows),
        "median_chars": lengths[len(lengths) // 2],
        "sections": ["서평을 찾게 된 경위", "변호사의 조력", "소송 결과", "사건의 의의"],
        "example_titles": [str(row.get("title", "")) for row in rows[:10]],
        "example_tags": [str(row.get("tag", "")) for row in rows[:10]],
        "example_body_openings": [str(row.get("body", ""))[:1200] for row in rows[:3]],
    }


def image_parts(uploads: list, max_total_bytes: int = 15 * 1024 * 1024) -> list[dict]:
    total = sum(len(upload.getvalue()) for upload in uploads)
    if total > max_total_bytes:
        raise ValueError("AI 분석에 전송할 이미지 합계는 15MB 이하여야 합니다. 등록 자체에는 더 많은 이미지를 첨부할 수 있습니다.")
    return [
        {
            "inline_data": {
                "mime_type": upload.type or "image/jpeg",
                "data": base64.b64encode(upload.getvalue()).decode("ascii"),
            }
        }
        for upload in uploads
    ]


def local_redact(text: str) -> tuple[str, list[dict]]:
    log: list[dict] = []
    clean = text
    for kind, pattern in PATTERNS:
        def repl(match: re.Match) -> str:
            log.append({
                "before": match.group(0),
                "after": f"[{kind} 삭제]",
                "reason": "AI 전송 전 로컬 개인정보 보호",
            })
            return f"[{kind} 삭제]"

        clean = re.sub(pattern, repl, clean)
    return clean, log


def _parse_json(text: str) -> dict:
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("AI 분석 결과를 읽지 못했습니다.")
    data = json.loads(text[start:end + 1])
    text_fields = (
        "category",
        "case_tag",
        "case_title",
        "title",
        "lawyer",
        "case_background",
        "lawyer_support",
        "case_result",
        "case_significance",
        "case_keyword",
    )
    list_fields = ("privacy_review", "change_log", "warnings", "missing_information")
    for field in text_fields:
        data.setdefault(field, "")
    for field in list_fields:
        data.setdefault(field, [])
    if data["category"] not in CATEGORIES:
        data["category"] = "민사·기업·손해배상"
    if isinstance(data["case_keyword"], list):
        data["case_keyword"] = " ".join(
            item if str(item).startswith("#") else f"#{item}"
            for item in map(str, data["case_keyword"])
            if item.strip()
        )
    return data


def organize(raw_text: str = "", current: dict | None = None, images: list[dict] | None = None) -> dict:
    source = json.dumps(current, ensure_ascii=False) if current else raw_text
    safe_source, local_log = local_redact(source)
    profile = style_profile()
    system = f"""당신은 법무법인 서평 홈페이지의 승소사례 편집자다.
현재 운영 중인 승소사례 {profile['sample_count']}건에서 확인한 작성 형식은 다음과 같다.
{json.dumps(profile, ensure_ascii=False)}

반드시 지킬 원칙:
- 입력에 없는 사실, 판결 결과, 죄명, 형량, 금액, 날짜, 인물 관계, 법률 판단을 만들거나 추측하지 않는다.
- 문체만 기존 사례처럼 정돈하고 사실관계는 바꾸지 않는다.
- 불명확하거나 빠진 정보는 내용을 지어내지 말고 해당 필드는 빈 문자열로 두며 missing_information에 한국어로 적는다.
- 판결 결과가 원문에 명시되지 않았다면 case_result를 빈 문자열로 둔다.
- 개인정보는 의뢰인, 상대방, A씨, B씨 등으로 익명화한다.
- category는 반드시 아래 기존 분류 중 하나만 선택한다: {json.dumps(CATEGORIES, ensure_ascii=False)}
- title은 상세페이지 제목이며 기존처럼 '[사건유형] 구체적 결과를 요약한 사례' 형태를 우선한다.
- case_title은 메인 슬라이드용 짧은 승소 제목이다. 예: '[손해배상] 상대방청구 전부 기각'.
- case_tag는 목록에 노출할 짧은 사건 분야 라벨이다. 예: '민사', '민사·손해배상', '형사'.
- 본문 고정 필드는 실제 홈페이지와 동일하게 case_background, lawyer_support, case_result, case_significance 네 개다.
- 각 본문 필드는 HTML로 작성한다. 문단은 <p>, 빈 간격은 <p class="blank"></p>, 핵심 소항목은 <p><strong>① ...</strong></p>를 사용한다.
- 결과를 과장하거나 성공을 보장하는 문구를 쓰지 않는다.
- 의미 있는 익명화와 수정은 change_log에 before, after, reason으로 기록한다.

JSON만 반환한다:
{{"category":"","case_tag":"","case_title":"","title":"","lawyer":"장진훈","case_background":"","lawyer_support":"","case_result":"","case_significance":"","case_keyword":"","privacy_review":[{{"type":"","finding":"","action":""}}],"change_log":[{{"before":"","after":"","reason":""}}],"warnings":[],"missing_information":[]}}"""
    prompt = safe_source or "첨부 이미지를 읽어 승소사례로 정리하세요."
    parts = [{"text": prompt}]
    parts.extend(images or [])
    text, error = gemini._call_gemini(
        system_instruction=system,
        contents=[{"role": "user", "parts": parts}],
        generation_config={"temperature": 0.1, "responseMimeType": "application/json"},
    )
    if error:
        raise RuntimeError(error)
    result = _parse_json(text)
    result["change_log"] = local_log + result["change_log"]
    return result
