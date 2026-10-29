"""Admin panel entry point (Streamlit multipage with sidebar navigation)."""

import os

import streamlit as st

import admin_common as ac

st.set_page_config(page_title="Frontline Safety Fear [PPE Scorecard]", layout="wide")

ac.inject_css()

ac.require_login()

# Logo + app name above the navigation menu
if os.path.exists("frontline-logo-white.png"):
    st.logo("frontline-logo-white.png", size="large")

pages = [
    st.Page("pages/home.py", title="Overview", icon=":material/home:"),
    st.Page("pages/1_Metrics.py", title="Metrics", icon=":material/bar_chart:"),
    st.Page("pages/2_Send_Campaign.py", title="Send Campaign", icon=":material/send:"),
    st.Page("pages/3_Import_Leads.py", title="Import Leads", icon=":material/upload:"),
    st.Page("pages/4_Config.py", title="Config", icon=":material/settings:"),
]

pg = st.navigation(pages, position="sidebar")
pg.run()
