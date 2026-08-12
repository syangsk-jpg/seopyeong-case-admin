"""AI-assisted creation for the existing Rhymix winning_cases board."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import mimetypes
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

import streamlit as st

import config
import winning_case_ai


ROOT = Path(__file__).resolve().parent
DRAFT_DIR = ROOT / "data" / "winning_case_drafts"
IMPORTER = ROOT / "winning_case_importer.php"
SITE_BASE_URL = os.getenv("SITE_URL", "https://xn--6l3bu5e7thckcqxywya.com").rstrip("/")

CATEGORIES = {
    "민사·기업·손해배상": 310,
    "음주·성범죄": 315,
    "형사·폭행·명예훼손·사기·마약": 311,
    "행정(소송·심판)": 314,
    "상속·종중": 309,
    "이혼·가사": 313,
}

FIELD_KEYS = (
    "case_background",
    "lawyer_support",
    "case_result",
    "case_significance",
)


def _env() -> dict[str, str]:
    values = {key: (os.getenv(key) or "") for key in ("SFTP_HOST", "SFTP_USER", "SFTP_PASSWORD")}
    credentials = Path(os.getenv("WEBSITE_REPO_ROOT", r"H:\coding\splawilsan_homepage")) / "_deploy" / "credentials.env"
    if credentials.is_file():
        for line in credentials.read_text(encoding="utf-8").splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if key.strip() in values and not values[key.strip()]:
                    values[key.strip()] = value.strip()
    return values


def _safe_name(name: str, index: int) -> str:
    suffix = Path(name).suffix.lower() or ".jpg"
    stem = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", Path(name).stem).strip("_") or f"image_{index + 1}"
    return f"{index + 1:02d}_{stem[:70]}{suffix}"


def _preview_image(upload) -> str:
    mime = upload.type or mimetypes.guess_type(upload.name)[0] or "image/jpeg"
    encoded = base64.b64encode(upload.getvalue()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _normalize_html(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if re.search(r"</?(?:p|ul|ol|li|blockquote|strong)\b", value, re.I):
        return value
    return "\n".join(f"<p>{html.escape(line.strip())}</p>" for line in value.splitlines() if line.strip())


def _errors(data: dict, images: list, final_check: bool) -> list[str]:
    errors = []
    if data["category"] not in CATEGORIES:
        errors.append("기존 승소사례 분류를 선택해 주세요.")
    if len(data["case_tag"].strip()) < 2:
        errors.append("목록용 라벨을 입력해 주세요.")
    if len(data["case_title"].strip()) < 6:
        errors.append("목록용 짧은 제목을 입력해 주세요.")
    if len(data["title"].strip()) < 8:
        errors.append("상세페이지 제목을 8자 이상 입력해 주세요.")
    for key, label in (
        ("case_background", "서평을 찾게 된 경위"),
        ("lawyer_support", "변호사의 조력"),
        ("case_result", "소송 결과"),
        ("case_significance", "사건의 의의"),
    ):
        if len(re.sub(r"<[^>]+>", "", data[key]).strip()) < 20:
            errors.append(f"{label} 항목을 확인해 주세요.")
    if not images:
        errors.append("이미지를 1장 이상 첨부해 주세요.")
    if not final_check:
        errors.append("사실관계와 개인정보 최종 확인이 필요합니다.")
    return errors


def _save_draft(data: dict) -> Path:
    DRAFT_DIR.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["uid"] = payload.get("uid") or datetime.now().strftime("%Y%m%d-%H%M%S")
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    path = DRAFT_DIR / f"{payload['uid']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _build_payload(data: dict, image_rows: list[dict], representative_index: int) -> dict:
    return {
        "uid": data["uid"],
        "category_srl": CATEGORIES[data["category"]],
        "title": data["title"].strip(),
        "tags": data["case_keyword"].strip(),
        "representative_index": representative_index,
        "images": image_rows,
        "extra_vars": {
            "lawyer": data["lawyer"].strip(),
            "case_background": _normalize_html(data["case_background"]),
            "lawyer_support": _normalize_html(data["lawyer_support"]),
            "case_result": _normalize_html(data["case_result"]),
            "case_significance": _normalize_html(data["case_significance"]),
            "case_title": data["case_title"].strip(),
            "case_tag": data["case_tag"].strip(),
            "case_keyword": _normalize_html(data["case_keyword"]),
        },
        # Existing winning_cases posts keep prose in extra vars 2-5.
        # The importer prepends only the uploaded image tags to content.
        "content": "",
    }


def _publish(data: dict, images: list, representative_index: int) -> tuple[int, str]:
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("paramiko가 설치되지 않았습니다.") from exc
    if not IMPORTER.is_file():
        raise RuntimeError("승소사례 등록기를 찾지 못했습니다.")
    env = _env()
    if any(not env.get(key) for key in ("SFTP_HOST", "SFTP_USER", "SFTP_PASSWORD")):
        raise RuntimeError("홈페이지 SFTP 설정이 완전하지 않습니다.")

    uid = data["uid"]
    remote_dir = f"www/files/_winning_case_admin/{uid}"
    image_rows = []
    with tempfile.TemporaryDirectory(prefix="winning_case_") as tmp:
        temp_dir = Path(tmp)
        for index, upload in enumerate(images):
            name = _safe_name(upload.name, index)
            path = temp_dir / name
            path.write_bytes(upload.getvalue())
            image_rows.append({"name": name, "source_name": upload.name, "mime": upload.type or ""})
        payload = _build_payload(data, image_rows, representative_index)
        payload_path = temp_dir / "post.json"
        payload_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(env["SFTP_HOST"], 22, env["SFTP_USER"], env["SFTP_PASSWORD"], timeout=40)
            client.exec_command(f"mkdir -p {remote_dir}")
            sftp = client.open_sftp()
            sftp.put(str(IMPORTER), "www/_winning_case_importer.php")
            sftp.put(str(payload_path), f"{remote_dir}/post.json")
            for row in image_rows:
                sftp.put(str(temp_dir / row["name"]), f"{remote_dir}/{row['name']}")
            sftp.close()
            command = (
                "cd www && /usr/local/php84/bin/php _winning_case_importer.php "
                f"$(pwd)/files/_winning_case_admin/{uid}/post.json "
                f"$(pwd)/files/_winning_case_admin/{uid}"
            )
            _, stdout, stderr = client.exec_command(command, timeout=240)
            output = stdout.read().decode("utf-8", "replace") + stderr.read().decode("utf-8", "replace")
            code = stdout.channel.recv_exit_status()
            match = re.search(r"^OK\s+(\d+)\s+", output, re.M)
            if code or not match:
                raise RuntimeError("홈페이지 등록에 실패했습니다. 서버 기록: " + output[-800:])
            document_srl = int(match.group(1))
            client.exec_command(
                "rm -f www/_winning_case_importer.php; "
                f"rm -rf {remote_dir}; "
                "rm -rf www/files/cache/widget_cache/* "
                "www/files/cache/template/modules/board/* "
                "www/files/cache/template/widgets/bh_gall_widget/*"
            )
            return document_srl, f"{SITE_BASE_URL}/winning_cases/{document_srl}"
        finally:
            try:
                client.exec_command(f"rm -f www/_winning_case_importer.php; rm -rf {remote_dir}")
            except Exception:
                pass
            client.close()


def _apply_ai_result(result: dict) -> None:
    mapping = {
        "wc_category": "category",
        "wc_case_tag": "case_tag",
        "wc_case_title": "case_title",
        "wc_title": "title",
        "wc_lawyer": "lawyer",
        "wc_background": "case_background",
        "wc_support": "lawyer_support",
        "wc_result": "case_result",
        "wc_significance": "case_significance",
        "wc_keywords": "case_keyword",
    }
    for state_key, result_key in mapping.items():
        st.session_state[state_key] = result.get(result_key, "")
    for key in ("privacy_review", "change_log", "warnings", "missing_information"):
        st.session_state[f"wc_{key}"] = result.get(key, [])


def _current_data() -> dict:
    return {
        "category": st.session_state.get("wc_category", next(iter(CATEGORIES))),
        "case_tag": st.session_state.get("wc_case_tag", ""),
        "case_title": st.session_state.get("wc_case_title", ""),
        "title": st.session_state.get("wc_title", ""),
        "lawyer": st.session_state.get("wc_lawyer", "장진훈"),
        "case_background": st.session_state.get("wc_background", ""),
        "lawyer_support": st.session_state.get("wc_support", ""),
        "case_result": st.session_state.get("wc_result", ""),
        "case_significance": st.session_state.get("wc_significance", ""),
        "case_keyword": st.session_state.get("wc_keywords", ""),
    }


def render_winning_case_admin() -> None:
    st.title("승소사례 등록")
    st.caption("원문과 이미지를 검토해 기존 홈페이지 승소사례 게시판에 새 글을 등록합니다.")
    st.warning("AI는 원문을 기존 형식에 맞게 정리할 뿐입니다. 판결 결과와 사실관계는 등록 전에 담당자가 반드시 확인해 주세요.")
    password = st.text_input("관리자 비밀번호 *", type="password", key="winning_case_admin_password")
    if password != config.get_dashboard_credentials()[1]:
        st.info("승소사례를 등록하려면 관리자 비밀번호를 입력해 주세요.")
        return

    profile = winning_case_ai.style_profile()
    with st.expander(f"기존 승소사례 구조 ({profile.get('sample_count', 0)}건 기준)"):
        st.write("고정 항목: 서평을 찾게 된 경위 → 변호사의 조력 → 소송 결과 → 사건의 의의")
        st.write("기존 분류: " + " / ".join(CATEGORIES))
        st.caption("상세 제목, 메인 슬라이드용 짧은 제목, 목록 라벨은 서로 다른 기존 필드로 저장됩니다.")

    st.subheader("1. 원문과 이미지")
    raw = st.text_area(
        "사건 관련 원문 *",
        height=220,
        key="wc_raw",
        placeholder="판결문 요약, 사건 경위, 수행 내용과 결과 등을 그대로 붙여 넣으세요.",
    )
    images = st.file_uploader(
        "관련 이미지 *",
        type=["png", "jpg", "jpeg", "webp"],
        accept_multiple_files=True,
        key="wc_images",
        help="최소 1장. 아래 순서대로 상세페이지에 표시되며 첫 번째 이미지가 기본 대표 이미지입니다.",
    ) or []
    image_ai_consent = True
    if images:
        image_ai_consent = st.checkbox(
            "첨부 이미지 원본이 내용 분석을 위해 Google Gemini로 전송되는 것에 동의합니다.",
            key="wc_image_ai_consent",
        )
        st.caption("이미지는 AI 분석에만 사용되며, 실제 등록 시에는 기존 홈페이지 첨부파일 저장소에도 업로드됩니다.")
    ordered_images = images
    representative_index = 0
    if images:
        names = [f"{index + 1}. {upload.name}" for index, upload in enumerate(images)]
        order_labels = st.multiselect(
            "이미지 순서",
            names,
            default=names,
            key="wc_image_order",
            help="선택한 순서가 상세페이지 노출 순서입니다. 빠진 이미지는 등록하지 않습니다.",
        )
        lookup = dict(zip(names, images))
        ordered_images = [lookup[label] for label in order_labels]
        if ordered_images:
            representative_label = st.selectbox(
                "대표 이미지",
                [f"{index + 1}. {upload.name}" for index, upload in enumerate(ordered_images)],
                key="wc_representative",
            )
            representative_index = int(representative_label.split(".", 1)[0]) - 1
            columns = st.columns(min(4, len(ordered_images)))
            for index, upload in enumerate(ordered_images):
                with columns[index % len(columns)]:
                    st.image(_preview_image(upload), caption=f"{index + 1}. {upload.name}", use_container_width=True)

    if st.button(
        "AI로 승소사례 분석",
        type="primary",
        use_container_width=True,
        disabled=not raw.strip() or not image_ai_consent,
    ):
        try:
            with st.spinner("기존 승소사례 형식에 맞춰 사실관계와 고정 항목을 정리하고 있습니다..."):
                result = winning_case_ai.organize(
                    raw_text=raw,
                    images=winning_case_ai.image_parts(ordered_images),
                )
            _apply_ai_result(result)
            st.success("AI 분석이 끝났습니다. 아래 내용을 직접 확인하고 수정해 주세요.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    st.subheader("2. AI 분석 결과 편집")
    st.selectbox("분류 *", list(CATEGORIES), key="wc_category")
    a, b = st.columns(2)
    a.text_input("목록 라벨 *", key="wc_case_tag", placeholder="예: 민사·손해배상")
    b.text_input("메인 슬라이드용 짧은 제목 *", key="wc_case_title", placeholder="예: [손해배상] 상대방청구 전부 기각")
    st.text_input("상세페이지 제목 *", key="wc_title", placeholder="예: [손해배상] 침수 피해 손해배상 청구를 방어한 사례")
    st.text_input("담당 변호사", key="wc_lawyer")
    st.text_area("서평을 찾게 된 경위 *", height=220, key="wc_background")
    st.text_area("변호사의 조력 *", height=260, key="wc_support")
    st.text_area("소송 결과 *", height=220, key="wc_result")
    st.text_area("사건의 의의 *", height=240, key="wc_significance")
    st.text_area("관련 키워드", height=90, key="wc_keywords", placeholder="#승소사례 #민사소송")

    current = _current_data()
    if st.button("현재 편집본을 AI로 다시 검토", use_container_width=True, disabled=not current["title"].strip()):
        try:
            with st.spinner("누락 정보와 사실관계 표현을 다시 검토하고 있습니다..."):
                result = winning_case_ai.organize(current=current)
            _apply_ai_result(result)
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    missing = st.session_state.get("wc_missing_information", [])
    warnings = st.session_state.get("wc_warnings", [])
    if missing:
        st.warning("원문에서 확인되지 않은 정보: " + " / ".join(map(str, missing)))
    if warnings:
        st.warning("AI 주의사항: " + " / ".join(map(str, warnings)))
    privacy = st.session_state.get("wc_privacy_review", [])
    changes = st.session_state.get("wc_change_log", [])
    with st.expander(f"AI 개인정보 검토 및 수정 기록 ({len(changes)}건)", expanded=bool(changes)):
        if privacy:
            st.markdown("**개인정보 검토**")
            st.dataframe(privacy, use_container_width=True, hide_index=True)
        if changes:
            st.markdown("**수정 전·후 기록**")
            st.dataframe(changes, use_container_width=True, hide_index=True)
        if not privacy and not changes:
            st.caption("아직 AI 검토 기록이 없습니다.")

    st.subheader("3. 미리보기와 등록")
    if ordered_images:
        st.image([_preview_image(upload) for upload in ordered_images], width=240)
    st.markdown(f"**{html.escape(current['case_tag'])}**")
    st.markdown(f"### {html.escape(current['title'])}")
    for label, key in (
        ("서평을 찾게 된 경위", "case_background"),
        (f"{current['lawyer'] or '담당 변호사'} 변호사의 조력", "lawyer_support"),
        ("소송 결과", "case_result"),
        ("사건의 의의", "case_significance"),
    ):
        st.markdown(f"#### {label}")
        section_html = _normalize_html(current[key])
        if section_html:
            st.html(section_html)
        else:
            st.caption("아직 작성된 내용이 없습니다.")

    final_check = st.checkbox("원문에 없는 내용이 추가되지 않았고 개인정보와 판결 결과를 사람이 최종 확인했습니다. *", key="wc_final_check")
    data = {**current, "change_log": changes, "privacy_review": privacy, "missing_information": missing}
    errors = _errors(data, ordered_images, final_check)
    left, right = st.columns(2)
    if left.button("임시저장", use_container_width=True):
        st.success(f"초안을 저장했습니다: {_save_draft(data).name}")
    confirmation = right.text_input("등록 확인", placeholder="승소사례등록 입력", label_visibility="collapsed")
    if st.button(
        "홈페이지 승소사례에 등록",
        type="primary",
        use_container_width=True,
        disabled=bool(errors) or confirmation != "승소사례등록",
    ):
        data["uid"] = hashlib.sha256(
            f"{data['title']}|{data['case_result']}".encode("utf-8")
        ).hexdigest()[:16]
        draft_path = DRAFT_DIR / f"{data['uid']}.json"
        if draft_path.exists() and json.loads(draft_path.read_text(encoding="utf-8")).get("status") == "published":
            st.error("이미 등록된 동일 내용입니다. 중복 게시하지 않았습니다.")
            return
        draft_path = _save_draft(data)
        try:
            with st.spinner("기존 홈페이지 승소사례 DB와 이미지 저장소에 등록하고 있습니다..."):
                document_srl, url = _publish(data, ordered_images, representative_index)
            data.update(status="published", document_srl=document_srl, url=url)
            draft_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            st.success(f"등록되었습니다. 글 번호: {document_srl}")
            st.link_button("등록된 승소사례 열기", url)
            st.link_button("기존 관리자에서 관리", f"{SITE_BASE_URL}/admin?module=document&act=dispDocumentAdminList")
        except Exception as exc:
            st.error(str(exc))
    elif errors:
        st.info("등록 전 확인: " + " ".join(errors))
