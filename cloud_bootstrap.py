import json,os,tempfile
from pathlib import Path
import streamlit as st
def prepare():
    try:
        for key in st.secrets:
            value=st.secrets[key]
            if isinstance(value,(str,int,float,bool)):os.environ[str(key)]=str(value)
    except Exception:
        pass
    raw=os.getenv("GA4_SERVICE_ACCOUNT_JSON","").strip()
    if raw:
        path=Path(tempfile.gettempdir())/"ga4-service-account.json"
        json.loads(raw)
        path.write_text(raw,encoding="utf-8")
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"]=str(path)
    os.environ.setdefault("LOGIN_REQUIRED","true")
    if not os.getenv("DASHBOARD_PASSWORD","").strip():
        raise RuntimeError("DASHBOARD_PASSWORD secret is required")