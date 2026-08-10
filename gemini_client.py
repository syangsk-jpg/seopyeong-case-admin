import os,requests
BASE="https://generativelanguage.googleapis.com/v1beta/models"
MODELS=["gemini-2.5-flash","gemini-2.5-flash-lite","gemini-flash-lite-latest"]
def _call_gemini(*,system_instruction,contents,generation_config=None):
    key=os.getenv("GEMINI_API_KEY","").strip()
    if not key:return "","Gemini API 키가 설정되지 않았습니다."
    payload={"systemInstruction":{"parts":[{"text":system_instruction}]},"contents":contents}
    if generation_config:payload["generationConfig"]=generation_config
    last=""
    for model in MODELS:
        try:r=requests.post(f"{BASE}/{model}:generateContent",params={"key":key},json=payload,timeout=120)
        except requests.RequestException as exc:last=str(exc);continue
        if r.ok:
            body=r.json();parts=body.get("candidates",[{}])[0].get("content",{}).get("parts",[])
            text="\n".join(str(p.get("text","")) for p in parts if isinstance(p,dict)).strip()
            if text:return text,None
        try:last=str(r.json().get("error",{}).get("message",""))[:200]
        except Exception:last=(r.text or "")[:200]
        if r.status_code not in (400,404,429,503):break
    return "",f"Gemini API 오류: {last or '응답 없음'}"