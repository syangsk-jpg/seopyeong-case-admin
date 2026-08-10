"""상담사례 초안·미리보기·Rhymix 공개 등록."""
import hashlib, html, json, os, re, tempfile
from datetime import datetime
from pathlib import Path
import streamlit as st
import config
import case_ai

ROOT=Path(__file__).resolve().parent
DRAFT_DIR=ROOT/"data"/"case_drafts"
SITE=Path(os.getenv("WEBSITE_REPO_ROOT",r"D:\coding\splawilsan_homepage"))
CREDS=SITE/"_deploy"/"credentials.env"
IMPORTER=SITE/"_deploy"/"gdrive_export"/"_import_case_posts_once.php"
CATEGORIES={"상속·종중·가사":441,"민사·손해배상":442,"형사":443,"조세":444,"이혼·상간":445,"기업·법률자문":446,"교통사고":447,"마약":448,"학교폭력":546}
STYLE="""<style>.sp-case{line-height:1.9;font-size:16px;color:#222;word-break:keep-all}.sp-case h2{margin:2em 0 .7em;font-size:1.28em;color:#12172B;border-bottom:2px solid #C49A4E}.sp-case p{margin:.75em 0}.sp-case strong{color:#12172B}</style>"""

def _env():
    return {k:(os.getenv(k) or "") for k in ("SFTP_HOST","SFTP_USER","SFTP_PASSWORD")}

def _inline(text):
    safe=html.escape(text)
    return re.sub(r"\*\*(.+?)\*\*",r"<strong>\1</strong>",safe)

def _content(summary,body):
    parts=[];items=[]
    def flush():
        nonlocal items
        if items:parts.append("<ul>"+"".join(f"<li>{_inline(x)}</li>" for x in items)+"</ul>");items=[]
    for raw in body.splitlines():
        line=raw.strip()
        if not line:flush();continue
        if line.startswith("- "):items.append(line[2:]);continue
        flush()
        if line.startswith("### "):parts.append(f"<h3>{_inline(line[4:])}</h3>")
        elif line.startswith("## "):parts.append(f"<h2>{_inline(line[3:])}</h2>")
        elif line.startswith("> "):parts.append(f"<blockquote><p>{_inline(line[2:])}</p></blockquote>")
        else:parts.append(f"<p>{_inline(line)}</p>")
    flush()
    return STYLE+'<div class="sp-case"><p><strong>'+html.escape(summary)+"</strong></p>"+"".join(parts)+'<h2>상담 안내</h2><p><strong>법무법인 서평</strong> · 상담문의 031-902-2100</p></div>'
def _errors(title,summary,body,anon):
    e=[]
    if len(title.strip())<8:e.append("제목을 8자 이상 입력해 주세요.")
    if len(summary.strip())<20:e.append("한 줄 요약을 20자 이상 입력해 주세요.")
    if len(body.strip())<100:e.append("본문을 100자 이상 입력해 주세요.")
    if not anon:e.append("개인정보 익명화 여부를 확인해 주세요.")
    return e

def _draft(data):
    DRAFT_DIR.mkdir(parents=True,exist_ok=True)
    data["uid"]=data.get("uid") or datetime.now().strftime("%Y%m%d-%H%M%S")
    data["updated_at"]=datetime.now().isoformat(timespec="seconds")
    p=DRAFT_DIR/f'{data["uid"]}.json'
    p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    return p

def _publish(data):
    try: import paramiko
    except ImportError as exc: raise RuntimeError("paramiko가 설치되지 않았습니다.") from exc
    if not IMPORTER.is_file(): raise RuntimeError("홈페이지 등록기를 찾지 못했습니다.")
    env=_env()
    if any(not env.get(k) for k in ("SFTP_HOST","SFTP_USER","SFTP_PASSWORD")): raise RuntimeError("SFTP 설정이 완전하지 않습니다.")
    post=[{"title":data["title"].strip(),"content":_content(data["summary"].strip(),data["body"].strip()),"category_srl":CATEGORIES[data["category"]],"case_label":data["category"],"tags":data["tags"].strip(),"thumb":""}]
    with tempfile.NamedTemporaryFile("w",suffix=".json",encoding="utf-8",delete=False) as f:
        json.dump(post,f,ensure_ascii=False); local=Path(f.name)
    c=paramiko.SSHClient(); c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        c.connect(env["SFTP_HOST"],22,env["SFTP_USER"],env["SFTP_PASSWORD"],timeout=40)
        c.exec_command("mkdir -p www/files/_case_admin_tmp")
        s=c.open_sftp(); s.put(str(IMPORTER),"www/_import_case_posts_once.php"); s.put(str(local),"www/files/_case_admin_tmp/post.json"); s.close()
        cmd="cd www && /usr/local/php84/bin/php _import_case_posts_once.php $(pwd)/files/_case_admin_tmp/post.json $(pwd)/files/_case_admin_tmp"
        _,o,e=c.exec_command(cmd,timeout=180); output=o.read().decode("utf-8","replace")+e.read().decode("utf-8","replace"); code=o.channel.recv_exit_status()
        m=re.search(r"^OK\s+(\d+)\s+",output,re.M)
        if code or not m: raise RuntimeError("홈페이지 등록에 실패했습니다. 서버 기록을 확인해 주세요.")
        doc=int(m.group(1))
        c.exec_command("rm -f www/_import_case_posts_once.php www/files/_case_admin_tmp/post.json; rm -rf www/files/cache/widget_cache/* www/files/cache/template/layouts/seopyeongis/*")
        return doc,f"/case_study/{doc}"
    finally: local.unlink(missing_ok=True); c.close()

def _apply_ai_result(result):
    st.session_state["ca_title"]=result.get("title","")
    st.session_state["ca_category"]=result.get("category","민사·손해배상")
    st.session_state["ca_summary"]=result.get("summary","")
    st.session_state["ca_body"]=result.get("body_markdown","")
    st.session_state["ca_tags"]=result.get("tags","")
    st.session_state["ca_change_log"]=result.get("change_log",[])
    st.session_state["ca_privacy_review"]=result.get("privacy_review",[])
    st.session_state["ca_warnings"]=result.get("warnings",[])

def render_case_admin():
    st.title("상담사례 등록 관리")
    st.caption("로우 데이터나 파일을 AI로 정리하거나, 아래 입력칸에서 직접 작성할 수 있습니다.")
    st.warning("AI는 입력 내용을 정리하는 도구입니다. 사실관계와 법률 판단은 게시 전에 담당자가 반드시 확인해 주세요.")
    admin_password=st.text_input("관리자 비밀번호 *",type="password",key="case_admin_password")
    if admin_password != config.get_dashboard_credentials()[1]:
        st.info("상담사례를 관리하려면 관리자 비밀번호를 입력해 주세요.")
        return

    profile=case_ai.style_profile()
    with st.expander(f"AI가 참고하는 기존 상담사례 형식 ({profile.get('sample_count',0)}건 분석)",expanded=False):
        st.write(f"본문 중간 길이: 약 {profile.get('median_chars',0):,}자")
        st.write(f"글 한 건당 큰 소제목 평균 {profile.get('average_h2',0)}개, 작은 소제목 평균 {profile.get('average_h3',0)}개")
        st.caption("구성: 문제 상황 한마디 → 사건 배경 → 핵심 쟁점 → 가능한 대응 → 변호사 조력 → 상담 안내")

    st.subheader("1. 원문 넣기")
    uploaded=st.file_uploader("텍스트 또는 이미지 파일",type=["txt","md","csv","png","jpg","jpeg","webp"],help="텍스트 파일은 로컬에서 개인정보 패턴을 먼저 가립니다. 이미지는 동의한 경우 원본이 Gemini로 전송됩니다.")
    raw=st.text_area("로우 데이터 붙여넣기",height=180,key="ca_raw",placeholder="상담 메모, 통화 정리, 사건 개요 등을 그대로 붙여 넣으세요.")
    is_image=bool(uploaded and ((uploaded.type or "").startswith("image/") or uploaded.name.lower().endswith((".png",".jpg",".jpeg",".webp"))))
    image_ok=True
    if is_image:
        image_ok=st.checkbox("원본 이미지가 개인정보 검토와 글 정리를 위해 Google Gemini로 전송되는 것에 동의합니다.",key="ca_image_consent")
    st.caption("AI 실행 시 텍스트는 전화번호·주민번호·계좌번호·이메일·사건번호를 이 컴퓨터에서 먼저 가린 뒤 전송합니다.")

    if st.button("AI로 상담사례 정리하기",type="primary",disabled=(not raw.strip() and uploaded is None) or not image_ok,use_container_width=True):
        try:
            file_text,image_part=case_ai.read_upload(uploaded)
            with st.spinner("기존 사례 형식에 맞춰 정리하고 개인정보를 검토하고 있습니다..."):
                result=case_ai.organize(raw_text="\n\n".join(x for x in (raw,file_text) if x.strip()),image_part=image_part)
            _apply_ai_result(result);st.success("AI 정리가 끝났습니다. 아래 내용을 직접 수정하고 미리보기로 확인해 주세요.");st.rerun()
        except Exception as exc:st.error(str(exc))

    st.subheader("2. 내용 수정")
    st.text_input("제목 *",key="ca_title",placeholder="예: 공사대금을 받지 못했을 때 어떻게 해야 하나요?")
    st.selectbox("분야 *",list(CATEGORIES),key="ca_category")
    st.text_area("한 줄 요약 *",height=90,key="ca_summary")
    st.text_area("본문 *",height=430,key="ca_body",placeholder="직접 작성하거나 위에서 AI 정리를 실행하세요.")
    st.text_input("검색어",key="ca_tags",placeholder="공사대금, 미지급, 내용증명")

    current={"title":st.session_state.get("ca_title",""),"category":st.session_state.get("ca_category","민사·손해배상"),"summary":st.session_state.get("ca_summary",""),"body_markdown":st.session_state.get("ca_body",""),"tags":st.session_state.get("ca_tags","")}
    if st.button("현재 편집본을 AI로 다시 검토",use_container_width=True,disabled=not current["body_markdown"].strip()):
        try:
            with st.spinner("개인정보, 빠진 내용, 어려운 표현을 다시 검토하고 있습니다..."):result=case_ai.organize(current=current)
            _apply_ai_result(result);st.rerun()
        except Exception as exc:st.error(str(exc))

    warnings=st.session_state.get("ca_warnings",[])
    if warnings:
        st.warning("AI 주의사항: "+" / ".join(map(str,warnings)))
    privacy=st.session_state.get("ca_privacy_review",[])
    changes=st.session_state.get("ca_change_log",[])
    with st.expander(f"AI 개인정보 검토 및 수정 로그 ({len(changes)}건)",expanded=bool(changes)):
        if privacy:
            st.markdown("**개인정보 검토**");st.dataframe(privacy,use_container_width=True,hide_index=True)
        if changes:
            st.markdown("**수정 전·후 기록**");st.dataframe(changes,use_container_width=True,hide_index=True)
        if not privacy and not changes:st.caption("아직 AI 검토 기록이 없습니다.")

    st.subheader("3. 미리보기와 공개")
    preview=st.toggle("홈페이지 모습 미리보기",value=True)
    if preview:
        st.components.v1.html(_content(current["summary"],current["body_markdown"]),height=620,scrolling=True)
        st.caption("위 수정칸을 고치면 미리보기도 갱신됩니다.")

    anon=st.checkbox("개인정보와 사실관계를 사람이 최종 확인했습니다. *",key="ca_final_check")
    data={"title":current["title"],"category":current["category"],"summary":current["summary"],"body":current["body_markdown"],"tags":current["tags"],"change_log":changes,"privacy_review":privacy}
    errors=_errors(data["title"],data["summary"],data["body"],anon)
    a,b=st.columns(2)
    if a.button("초안 저장",use_container_width=True):
        st.success(f"초안을 저장했습니다: {_draft(data).name}")
    confirm=b.text_input("공개 확인",placeholder="공개등록 입력",label_visibility="collapsed")
    if st.button("홈페이지에 공개 등록",type="primary",use_container_width=True,disabled=bool(errors) or confirm!="공개등록"):
        data["uid"]=hashlib.sha256(f'{data["title"]}|{data["body"]}'.encode()).hexdigest()[:12]
        existing=DRAFT_DIR/f'{data["uid"]}.json'
        if existing.exists() and json.loads(existing.read_text(encoding="utf-8")).get("status")=="published":
            st.error("이미 공개 등록된 내용입니다. 중복으로 게시하지 않았습니다.");return
        draft=_draft(data)
        try:
            with st.spinner("홈페이지에 등록하고 있습니다..."):doc,url=_publish(data)
            data.update(status="published",document_srl=doc,url=url);draft.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
            st.success(f"등록되었습니다. 글 번호: {doc}");st.link_button("등록된 상담사례 열기",url)
        except Exception as exc:st.error(str(exc))
    elif errors:st.info("공개 등록 전 확인: "+" ".join(errors))