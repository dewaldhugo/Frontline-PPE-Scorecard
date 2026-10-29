import os

import streamlit as st

import admin_common as ac

ac.inject_css()

st.title("⚙️ Config")

env_path = ".env"
if os.path.exists(env_path):
    st.code(open(env_path).read(), language="bash")
else:
    st.warning(".env not found.")
st.info("Edit .env directly to change SMTP credentials, sender details, etc.")
