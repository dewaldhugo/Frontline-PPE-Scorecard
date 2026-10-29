import os

import pandas as pd
import streamlit as st

import admin_common as ac

ac.inject_css()

st.title("📥 Import Leads")

sector_imp = st.selectbox("Target sector", ac.SECTORS, index=0, key="imp_sector")
uploaded = st.file_uploader("Upload CSV (columns: email, first_name, company)", type=["csv"])
mode = st.radio("Import mode", ["Append to existing", "Replace existing"], horizontal=True)

if uploaded is not None:
    try:
        pdf = pd.read_csv(uploaded)
        st.dataframe(pdf.head(20), use_container_width=True)
        st.write(f"Rows: {len(pdf)}")
        if "email" not in pdf.columns or "first_name" not in pdf.columns:
            st.error("CSV must contain 'email' and 'first_name' columns.")
        else:
            if st.button("Save imported leads"):
                target = f"leads_{sector_imp}.csv"
                if mode == "Append to existing" and os.path.exists(target):
                    existing = pd.read_csv(target)
                    combined = pd.concat([existing, pdf], ignore_index=True)
                    combined = combined.drop_duplicates(subset=["email"], keep="first")
                else:
                    combined = pdf
                combined.to_csv(target, index=False)
                st.success(f"Saved {len(combined)} leads to {target}")
    except Exception as e:
        st.error(f"Failed to read CSV: {e}")
