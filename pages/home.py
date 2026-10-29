import streamlit as st

import admin_common as ac

ac.inject_css()

st.title("Overview")
st.write("Welcome to the Frontline Safety Fear [PPE Scorecard] admin panel.")

df = ac.load_data()
if not df.empty:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total records", len(df))
    c2.metric("Live sends", int((df["dry_run"] == 0).sum()))
    c3.metric("Success", int((df["status"] == "SUCCESS").sum()))
    c4.metric("Skipped", int((df["status"] == "SKIPPED").sum()))
    st.subheader("Recent sends")
    st.dataframe(df.head(10)[["timestamp", "sector", "lead_email", "status", "dry_run"]],
                 use_container_width=True)
