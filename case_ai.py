"""Gemini 기반 상담사례 구조화·익명화."""
import base64,json,re
from pathlib import Path
import gemini_client as gemini
SITE=Path(r"D:\coding\splawilsan_homepage")
CASES=SITE/"_deploy"/"case_pipeline"/"staging"/"cases.json"
ALLOWED=["상속·종중·가사","민사·손해배상","형사","조세","이혼·상간","기업·법률자문","교통사고","마약","학교폭력"]
PATTERNS=[
("전화번호",r"(?<!\d)(?:01[016789]|0\d{1,2})[- .]?\d{3,4}[- .]?\d{4}(?!\d)"),
("이메일",r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
("주민등록번호",r"(?<!\d)\d{6}[- ]?[1-4]\d{6}(?!\d)"),
("계좌·카드번호",r"(?<!\d)\d{2,6}[- ]\d{2,6}[- ]\d{2,6}(?:[- ]\d{1,6})?(?!\d)"),
("사건번호",r"\b(?:19|20)\d{2}\s*[가-힣]{1,5}\s*\d{2,10}\b"),
]
def style_profile(n=20):
    try: rows=json.loads(CASES.read_text(encoding="utf-8"))[:n]
    except Exception: rows=[]
    if not rows:return {"sample_count":20,"median_chars":1990,"average_h2":1.0,"average_h3":4.0,"example_titles":[],"format":"상황→배경→쟁점→대응→상담"}
    lens=sorted(int(x.get("plain_len",0)) for x in rows); stats=[x.get("stats",{}) for x in rows]
    return {"sample_count":len(rows),"median_chars":lens[len(lens)//2],"average_h2":round(sum(int(x.get("h2",0)) for x in stats)/len(rows),1),"average_h3":round(sum(int(x.get("h3",0)) for x in stats)/len(rows),1),"example_titles":[x.get("title","") for x in rows[:6]]}
def local_redact(text):
    log=[];clean=text
    for kind,pattern in PATTERNS:
        def repl(m):
            log.append({"before":m.group(0),"after":f"[{kind} 삭제]","reason":"Gemini 전송 전 로컬 개인정보 보호"})
            return f"[{kind} 삭제]"
        clean=re.sub(pattern,repl,clean)
    return clean,log
def read_upload(uploaded):
    if uploaded is None:return "",None
    raw=uploaded.getvalue();mime=(uploaded.type or "").lower();name=uploaded.name.lower()
    if mime.startswith("image/") or name.endswith((".png",".jpg",".jpeg",".webp")):
        return "",{"inline_data":{"mime_type":mime or "image/jpeg","data":base64.b64encode(raw).decode()}}
    for enc in ("utf-8-sig","cp949","euc-kr"):
        try:return raw.decode(enc),None
        except UnicodeDecodeError:pass
    raise ValueError("텍스트 파일을 읽지 못했습니다.")
def _parse(text):
    a=text.find("{");b=text.rfind("}")
    if a<0 or b<a:raise ValueError("AI 정리 결과를 읽지 못했습니다.")
    d=json.loads(text[a:b+1])
    for k in ("title","category","summary","body_markdown","tags","change_log","privacy_review","warnings"):
        d.setdefault(k,[] if k in ("change_log","privacy_review","warnings") else "")
    if d["category"] not in ALLOWED:d["category"]="민사·손해배상"
    if isinstance(d["tags"],list):d["tags"]=", ".join(map(str,d["tags"]))
    return d
def organize(raw_text="",image_part=None,current=None):
    source=json.dumps(current,ensure_ascii=False) if current else raw_text
    safe,local_log=local_redact(source);profile=style_profile()
    system=f"""법무법인 홈페이지 상담사례 편집자 역할이다.
기존 공개 사례 {profile['sample_count']}건의 형식 분석: {json.dumps(profile,ensure_ascii=False)}
제공된 입력에 없는 사실·결과·날짜·금액·법률 판단을 만들지 않는다.
사람 이름은 의뢰인/상대방/A씨/B씨, 상세 주소는 지역 수준, 전화·계좌·차량·사건번호는 [삭제]로 익명화한다.
본문은 > 짧은 상황문, ## 사건 배경, ## 핵심 쟁점, ## 가능한 대응 방법, ## 변호사 조력이 필요한 이유 순서의 쉬운 한국어 마크다운으로 쓴다.
과장·승소 보장을 금지한다. 모든 익명화와 의미 있는 수정은 change_log에 before, after, reason으로 기록한다.
JSON만 반환: {{"title":"","category":"민사·손해배상","summary":"","body_markdown":"","tags":[],"privacy_review":[{{"type":"","finding":"","action":""}}],"change_log":[{{"before":"","after":"","reason":""}}],"warnings":[]}}
category는 {json.dumps(ALLOWED,ensure_ascii=False)} 중 하나다."""
    parts=[{"text":safe or "첨부 이미지를 읽어 상담사례로 정리하세요."}]
    if image_part:parts.append(image_part)
    text,err=gemini._call_gemini(system_instruction=system,contents=[{"role":"user","parts":parts}],generation_config={"temperature":0.2,"responseMimeType":"application/json"})
    if err:raise RuntimeError(err)
    d=_parse(text);d["change_log"]=local_log+d["change_log"];return d