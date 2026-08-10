import streamlit as st
import case_admin
st.set_page_config(page_title="서평 상담사례 등록",page_icon="📝",layout="wide",initial_sidebar_state="collapsed")
case_admin.render_case_admin()