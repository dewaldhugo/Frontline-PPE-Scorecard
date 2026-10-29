"""Shared helpers for the admin panel pages."""

import os
import bcrypt
import sqlite3
import subprocess
import sys

import pandas as pd

DB_PATH = os.getenv("DB_PATH", "data/campaign.db")
PYTHON = sys.executable
SECTORS = ["general", "agriculture", "hospitality"]


def run_cmd(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
    return proc.returncode, proc.stdout, proc.stderr


def load_data():
    import sqlite3
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query("SELECT * FROM sends ORDER BY id DESC", conn)
    finally:
        conn.close()
    if not df.empty and "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    return df


import streamlit as st


def inject_css():
    st.markdown(
        """
        <style>
        .block-container { padding-top: 1.5rem; }
        .metric-card { background: #ffffff; border-radius: 8px; padding: 16px;
                       box-shadow: 0 2px 6px rgba(0,0,0,0.08); }
        section[data-testid="stSidebar"] { background: #1e293b; }
        section[data-testid="stSidebar"] * { color: #e2e8f0 !important; }
        section[data-testid="stSidebar"] a { color: #93c5fd !important; }
        .stButton>button { border-radius: 6px; }
        h1, h2, h3 { color: #1e293b; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _hash_password(password):
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _check_password(password, hashed):
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


def init_users_table():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def seed_admin():
    init_users_table()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT password_hash FROM users WHERE email=?", ("dev@frontlinefootwear.com",)).fetchone()
        hashed = _hash_password("Success@27!")
        if row is None:
            conn.execute(
                "INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)",
                ("Admin", "dev@frontlinefootwear.com", hashed),
            )
        else:
            # Migrate legacy (non-bcrypt) hashes to bcrypt.
            if not row[0].startswith("$2"):
                conn.execute(
                    "UPDATE users SET password_hash=? WHERE email=?",
                    (hashed, "dev@frontlinefootwear.com"),
                )
        conn.commit()
    finally:
        conn.close()


def authenticate(email, password):
    init_users_table()
    seed_admin()
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute(
            "SELECT name, password_hash FROM users WHERE email=?", (email.strip().lower(),)
        ).fetchone()
    finally:
        conn.close()
    if not row:
        return None
    name, pw_hash = row
    if _check_password(password, pw_hash):
        return name
    return None


def require_login():
    seed_admin()
    if st.session_state.get("authenticated"):
        with st.sidebar:
            st.markdown(f"Logged in as **{st.session_state.get('user_name', '')}**")
            if st.button("Log out"):
                st.session_state.authenticated = False
                st.session_state.user_name = None
                st.rerun()
        return True

    st.title("Login")
    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in")
    if submitted:
        name = authenticate(email, password)
        if name:
            st.session_state.authenticated = True
            st.session_state.user_name = name
            st.rerun()
        else:
            st.error("Invalid email or password.")

    with st.expander("Forgot password?"):
        reset_email = st.text_input("Account email", key="reset_email")
        if st.button("Send reset link"):
            token = request_password_reset(reset_email)
            if token:
                st.success("Reset token generated. Use it below to set a new password.")
                st.code(token)
            else:
                st.error("No account found for that email.")
        st.markdown("---")
        token_input = st.text_input("Reset token", key="reset_token")
        new_pw = st.text_input("New password", type="password", key="new_pw")
        if st.button("Reset password"):
            if reset_password(token_input, new_pw):
                st.success("Password updated. Please log in.")
            else:
                st.error("Invalid or expired token.")

    st.stop()
    return False


def request_password_reset(email):
    """Generate a reset token for the given email (returns token or None)."""
    import secrets
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT id FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        if not row:
            return None
        token = secrets.token_urlsafe(32)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS reset_tokens (token TEXT PRIMARY KEY, email TEXT NOT NULL, expires_at TEXT NOT NULL)"
        )
        conn.execute("DELETE FROM reset_tokens WHERE email=?", (email.strip().lower(),))
        from datetime import datetime, timedelta
        expires = (datetime.utcnow() + timedelta(hours=1)).isoformat()
        conn.execute("INSERT INTO reset_tokens (token, email, expires_at) VALUES (?, ?, ?)",
                     (token, email.strip().lower(), expires))
        conn.commit()
        return token
    finally:
        conn.close()


def reset_password(token, new_password):
    """Reset password using a valid token. Returns True on success."""
    from datetime import datetime
    conn = sqlite3.connect(DB_PATH)
    try:
        row = conn.execute("SELECT email, expires_at FROM reset_tokens WHERE token=?", (token,)).fetchone()
        if not row:
            return False
        email, expires = row
        if datetime.utcnow().isoformat() > expires:
            conn.execute("DELETE FROM reset_tokens WHERE token=?", (token,))
            conn.commit()
            return False
        conn.execute("UPDATE users SET password_hash=? WHERE email=?", (_hash_password(new_password), email))
        conn.execute("DELETE FROM reset_tokens WHERE token=?", (token,))
        conn.commit()
        return True
    finally:
        conn.close()

