import os
import subprocess
import sys
import threading
import time

import pandas as pd
import streamlit as st

import admin_common as ac

ac.inject_css()
st.title("🚀 Send Campaign")

# ---------------- Send section
st.subheader("Execute a send")
col1, col2 = st.columns(2)
with col1:
    sector = st.selectbox("Sector", ac.SECTORS, index=0, key="send_sector")
    volume = st.number_input("Volume (max leads to process)", min_value=1, max_value=250, value=10, step=1, key="volume")
with col2:
    dry_run = st.checkbox("Dry run", value=True, key="dry_run")
    live = st.checkbox("Force live (overrides DRY_RUN)", value=False, key="live")

# session state for background run
if "run_active" not in st.session_state:
    st.session_state.run_active = False
if "run_output" not in st.session_state:
    st.session_state.run_output = []
if "run_proc" not in st.session_state:
    st.session_state.run_proc = None
if "run_thread" not in st.session_state:
    st.session_state.run_thread = None
if "run_total" not in st.session_state:
    st.session_state.run_total = 0
if "run_cancelled" not in st.session_state:
    st.session_state.run_cancelled = False
if "run_status" not in st.session_state:
    st.session_state.run_status = ""

def _reader(proc, bucket):
    for line in proc.stdout:
        bucket.append(line.rstrip("\n"))
    proc.stdout.close()
    proc.wait()

if st.button("Run send", type="primary", disabled=st.session_state.run_active):
    cmd = [ac.PYTHON, "send_campaign.py", "--sector", sector, "--limit", str(volume)]
    if live:
        cmd.append("--live")
    elif dry_run:
        cmd.append("--dry-run")
    st.session_state.run_output = []
    st.session_state.run_total = int(volume)
    st.session_state.run_cancelled = False
    st.session_state.run_status = "running"
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, cwd=os.getcwd())
    st.session_state.run_proc = proc
    t = threading.Thread(target=_reader, args=(proc, st.session_state.run_output), daemon=True)
    st.session_state.run_thread = t
    t.start()
    st.session_state.run_active = True
    st.rerun()

if st.session_state.run_active:
    thread = st.session_state.run_thread
    proc = st.session_state.run_proc
    out = st.session_state.run_output
    total = st.session_state.run_total or 1

    # count processed
    done = sum(1 for l in out if any(tag in l for tag in ("[SUCCESS]", "[FAILED]", "[SKIP]", "[WARN]", "[DRY RUN]")))
    progress = min(done / total, 1.0)
    st.progress(progress, text=f"Processed {done}/{total}")

    # ETA
    if done > 0 and thread.is_alive():
        # rough: use number of lines as proxy
        st.caption(f"ETA: running... ({len(out)} log lines)")

    st.text_area("Output", "\n".join(out), height=300)

    colc1, colc2 = st.columns(2)
    with colc1:
        if st.button("Cancel", type="secondary"):
            st.session_state.run_cancelled = True
            try:
                proc.kill()
            except Exception:
                pass
    with colc2:
        if not thread.is_alive():
            st.session_state.run_active = False
            st.session_state.run_status = "finished"
            ac.load_data.clear()
            st.success("Run finished.")
            st.rerun()

    if thread.is_alive():
        time.sleep(1)
        st.rerun()
