import streamlit as st

import admin_common as ac

ac.inject_css()

st.title("📊 Metrics")

df = ac.load_data()
if df.empty:
    st.warning(f"No data found in {ac.DB_PATH}.")
else:
    st.sidebar.header("Filters")
    status_filter = st.sidebar.multiselect("Status", sorted(df["status"].dropna().unique().tolist()),
                                           default=sorted(df["status"].dropna().unique().tolist()))
    company_filter = st.sidebar.multiselect("Company", sorted(df["company"].dropna().unique().tolist()),
                                            default=sorted(df["company"].dropna().unique().tolist()))
    sector_filter = st.sidebar.multiselect("Sector", sorted(df["sector"].dropna().unique().tolist()),
                                           default=sorted(df["sector"].dropna().unique().tolist()))
    dry_filter = st.sidebar.radio("Send type", ["All", "Live only", "Dry runs only"])

    filtered = df.copy()
    if status_filter:
        filtered = filtered[filtered["status"].isin(status_filter)]
    if company_filter:
        filtered = filtered[filtered["company"].isin(company_filter)]
    if sector_filter:
        filtered = filtered[filtered["sector"].isin(sector_filter)]
    if dry_filter == "Live only":
        filtered = filtered[filtered["dry_run"] == 0]
    elif dry_filter == "Dry runs only":
        filtered = filtered[filtered["dry_run"] == 1]

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Total", len(filtered))
    c2.metric("Success", int((filtered["status"] == "SUCCESS").sum()))
    c3.metric("Failed", int((filtered["status"] == "FAILED").sum()))
    c4.metric("Skipped", int((filtered["status"] == "SKIPPED").sum()))
    c5.metric("Live sends", int((filtered["dry_run"] == 0).sum()))

    st.subheader("Sends")
    cols = ["timestamp", "sector", "lead_email", "first_name", "company",
            "status", "dry_run", "smtp_response_code", "message_id", "error_message"]

    page_size = st.selectbox("Rows per page", [10, 25, 50, 100], index=0)
    total_rows = len(filtered)
    total_pages = max(1, (total_rows + page_size - 1) // page_size)
    page = st.number_input("Page", min_value=1, max_value=total_pages, value=1, step=1)
    start = (page - 1) * page_size
    end = start + page_size
    st.caption(f"Showing {start + 1}-{min(end, total_rows)} of {total_rows}")
    st.dataframe(filtered[cols].iloc[start:end], use_container_width=True)

    csv_data = filtered[cols].to_csv(index=False).encode("utf-8")
    st.download_button("Export to CSV", data=csv_data, file_name="sends_export.csv",
                       mime="text/csv")

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("By status")
        st.bar_chart(filtered["status"].value_counts())
    with col_b:
        st.subheader("By sector")
        st.bar_chart(filtered["sector"].value_counts())
