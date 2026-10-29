#!/usr/bin/env python3
"""
send_campaign.py

Reads a CSV of email leads and sends personalized multipart emails over SMTP.

Configuration is loaded from environment variables (see .env.example).
Email body copy is loaded from external template files
(email_template.html / email_template.txt).

Template placeholders use double-brace notation, e.g. {{first_name}},
{{company}}, {{email}}, {{survey_url}}. Any CSV column becomes available
as a placeholder with the same name.
"""

import csv
import io
import mimetypes
import os
import random
import re
import smtplib
import sqlite3
import time
import argparse
from email.utils import make_msgid
from datetime import datetime
from email import encoders
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------


def get_env(key, default=None, required=False):
    val = os.getenv(key, default)
    if required and (val is None or str(val).strip() == ""):
        raise EnvironmentError(f"Missing required environment variable: {key}")
    return val


def to_bool(val):
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    return str(val).strip().lower() in ("true", "1", "yes", "y", "on")


# ---------------------------------------------------------------------------
# Required configuration
# ---------------------------------------------------------------------------
SMTP_SERVER = get_env("SMTP_SERVER", required=True)
SMTP_PORT = int(get_env("SMTP_PORT", required=True))
SMTP_USE_TLS = to_bool(get_env("SMTP_USE_TLS", "True"))
SMTP_USERNAME = get_env("SMTP_USERNAME", required=True)
SMTP_PASSWORD = get_env("SMTP_PASSWORD", required=True)
SENDER_EMAIL = get_env("SENDER_EMAIL", required=True)
SENDER_NAME = get_env("SENDER_NAME", required=True)
SCOREAPP_SURVEY_URL = get_env("SCOREAPP_SURVEY_URL", required=True)

EMAIL_SUBJECT = get_env("EMAIL_SUBJECT", default="{{first_name}}, is PPE supply a risk on your projects?")
CURRENT_SUBJECT = EMAIL_SUBJECT
CSV_FILE_PATH = get_env("CSV_FILE_PATH", default="leads.csv")
DRY_RUN = to_bool(get_env("DRY_RUN", "True"))

HTML_TEMPLATE_PATH = get_env("HTML_TEMPLATE_PATH", default="email_template.html")
TEXT_TEMPLATE_PATH = get_env("TEXT_TEMPLATE_PATH", default="email_template.txt")
LOG_FILE_PATH = get_env("LOG_FILE_PATH", default="logs/campaign_log.csv")
DB_PATH = get_env("DB_PATH", default="data/campaign.db")

# Fallback values for known fields when a CSV cell is blank.
FIELD_FALLBACKS = {
    "first_name": "there",
    "company": "your company",
}

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# Test addresses that must always be included (bypass dedup / already-contacted).
WHITELIST_EMAILS = {
    e.strip().lower()
    for e in get_env(
        "WHITELIST_EMAILS",
        default="dewaldhugo@gmail.com,gavinlangwelsh27@gmail.com",
    ).split(",")
    if e.strip()
}

SECTORS = ("general", "agriculture", "hospitality")


def sector_config(sector):
    """Resolve per-sector file paths and copy settings."""
    sector = sector.lower().strip()
    if sector not in SECTORS:
        raise ValueError(f"Unknown sector: {sector}. Choose from {SECTORS}")

    leads_path = get_env(f"LEADS_FILE_{sector.upper()}", default=f"leads_{sector}.csv")
    html_path = get_env(f"HTML_TEMPLATE_{sector.upper()}", default=f"email_template_{sector}.html")
    txt_path = get_env(f"TEXT_TEMPLATE_{sector.upper()}", default=f"email_template_{sector}.txt")
    survey_url = get_env(f"SCOREAPP_SURVEY_URL_{sector.upper()}", default=get_env("SCOREAPP_SURVEY_URL", required=True))
    subject = get_env(f"EMAIL_SUBJECT_{sector.upper()}", default=get_env("EMAIL_SUBJECT", default="{{first_name}}, is PPE supply a risk on your projects?"))
    return {
        "sector": sector,
        "leads_path": leads_path,
        "html_path": html_path,
        "txt_path": txt_path,
        "survey_url": survey_url,
        "subject": subject,
    }
PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")


def validate_email(email):
    return bool(email) and bool(EMAIL_RE.match(email.strip()))


# ---------------------------------------------------------------------------
# Template handling
# ---------------------------------------------------------------------------


def load_template(path):
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Template file not found: {path}")
    return p.read_text(encoding="utf-8")


def build_context(row, survey_url=None):
    """Build a placeholder context from a CSV row plus injected values."""
    if survey_url is None:
        survey_url = get_env("SCOREAPP_SURVEY_URL", required=True)
    context = {}
    for key, value in (row or {}).items():
        if key is None:
            continue
        key = key.strip()
        val = (value or "").strip() if isinstance(value, str) else value
        if val == "" and key in FIELD_FALLBACKS:
            val = FIELD_FALLBACKS[key]
        context[key] = val if val is not None else ""

    # Ensure standard fields exist.
    if "email" not in context or not context.get("email"):
        context["email"] = ""
    if "first_name" not in context or not context.get("first_name"):
        context["first_name"] = FIELD_FALLBACKS["first_name"]
    if "company" not in context or not context.get("company"):
        context["company"] = FIELD_FALLBACKS["company"]

    context["survey_url"] = survey_url
    return context


def render_template(template, context):
    """Replace {{placeholder}} tokens using values from context."""

    def replace(match):
        key = match.group(1)
        if key in context:
            return str(context[key])
        # Unknown placeholder: leave empty but warn once via stderr? Keep quiet.
        return ""

    return PLACEHOLDER_RE.sub(replace, template)


# ---------------------------------------------------------------------------
# Image embedding helpers
# ---------------------------------------------------------------------------


def _resolve_image_path(src, template_path):
    """Resolve a relative image src to an existing file path."""
    if not src or src.startswith(("http://", "https://", "cid:", "data:")):
        return None
    candidates = []
    template_dir = Path(template_path).resolve().parent
    candidates.append(template_dir / src)
    candidates.append(Path.cwd() / src)
    candidates.append(Path(src))
    for cand in candidates:
        if cand.exists() and cand.is_file():
            return cand
    return None


def _compress_image(path, max_width=420, quality=62):
    """Return (bytes, mime_type) for an image (no compression; original quality)."""
    data = path.read_bytes()
    mime, _ = mimetypes.guess_type(str(path))
    return data, mime or "application/octet-stream"


def embed_images(html, template_path, budget_bytes=95000):
    """Rewrite local <img src> to cid: references.

    Returns (html, images) where images is a list of (cid, data, mime_type).
    Images are compressed; if total exceeds budget, a second pass reduces size.
    """
    # First pass: collect paths.
    matches = []
    def collect(match):
        matches.append((match, match.group(2)))
        return match.group(0)
    re.sub(r'(src\s*=\s*["\'])([^"\']+)(["\'])', collect, html, flags=re.IGNORECASE)

    # Resolve and compress.
    resolved = []  # list of (match_obj, path)
    for m in re.finditer(r'(src\s*=\s*["\'])([^"\']+)(["\'])', html, flags=re.IGNORECASE):
        path = _resolve_image_path(m.group(2), template_path)
        if path is not None:
            resolved.append((m, path))

    # Build cid list and compress.
    found = []  # (cid, data, mime)
    cid_set = set()
    total = 0
    for m, path in resolved:
        cid = path.name
        data, mime = _compress_image(path)
        if cid not in cid_set:
            cid_set.add(cid)
            found.append((cid, data, mime))
            total += len(data)

    # If over budget, recompress more aggressively.
    if total > budget_bytes and PIL_AVAILABLE:
        found = []
        cid_set = set()
        total = 0
        for m, path in resolved:
            cid = path.name
            data, mime = _compress_image(path, max_width=300, quality=45)
            if cid not in cid_set:
                cid_set.add(cid)
                found.append((cid, data, mime))
                total += len(data)

    # Rewrite src to cid: references.
    def replacer(match):
        prefix = match.group(1)
        src = match.group(2)
        suffix = match.group(3)
        path = _resolve_image_path(src, template_path)
        if path is None:
            return match.group(0)
        cid = path.name
        return f'{prefix}cid:{cid}{suffix}'

    new_html = re.sub(r'(src\s*=\s*["\'])([^"\']+)(["\'])', replacer, html, flags=re.IGNORECASE)
    return new_html, found


# Email construction
# ---------------------------------------------------------------------------


def build_message(lead_email, context, html_template, text_template, html_template_path,
                  message_id=None):
    if message_id is None:
        message_id = make_msgid(domain=SENDER_EMAIL.split("@")[-1])
    msg = MIMEMultipart("related")
    msg["Subject"] = render_template(CURRENT_SUBJECT, context)
    msg["From"] = f"{SENDER_NAME} <{SENDER_EMAIL}>"
    msg["To"] = lead_email
    msg["Message-ID"] = message_id

    alt = MIMEMultipart("alternative")
    text_body = render_template(text_template, context)
    html_body = render_template(html_template, context)
    html_body, images = embed_images(html_body, html_template_path)

    alt.attach(MIMEText(text_body, "plain", "utf-8"))
    alt.attach(MIMEText(html_body, "html", "utf-8"))
    msg.attach(alt)

    for cid, data, mime in images:
        if mime and mime.startswith("image/"):
            subtype = mime.split("/", 1)[1]
            img = MIMEImage(data, _subtype=subtype)
        else:
            img = MIMEBase("application", "octet-stream")
            img.set_payload(data)
            encoders.encode_base64(img)
        img.add_header("Content-ID", f"<{cid}>")
        img.add_header("Content-Disposition", "inline", filename=cid)
        msg.attach(img)

    return msg, message_id


# ---------------------------------------------------------------------------
# SMTP connection handling
# ---------------------------------------------------------------------------


def connect_smtp():
    # Port 465 implies implicit TLS (SMTP_SSL); 587 uses STARTTLS.
    if SMTP_PORT == 465:
        server = smtplib.SMTP_SSL(SMTP_SERVER, SMTP_PORT, timeout=30)
    elif SMTP_USE_TLS:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
        server.starttls()
    else:
        server = smtplib.SMTP(SMTP_SERVER, SMTP_PORT, timeout=30)
    server.login(SMTP_USERNAME, SMTP_PASSWORD)
    return server


def safe_quit(server):
    if server is None:
        return
    try:
        server.quit()
    except Exception:
        try:
            server.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def init_db(db_path=DB_PATH):
    """Create the metrics table if it does not exist (with migrations)."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                sector TEXT NOT NULL DEFAULT 'general',
                lead_email TEXT NOT NULL,
                first_name TEXT,
                company TEXT,
                status TEXT NOT NULL,
                error_message TEXT,
                subject TEXT,
                dry_run INTEGER NOT NULL DEFAULT 0,
                smtp_server TEXT,
                message_id TEXT,
                smtp_response_code INTEGER,
                smtp_response_message TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        cols = {r[1] for r in conn.execute("PRAGMA table_info(sends)").fetchall()}
        migrations = [
            ("sector", "ALTER TABLE sends ADD COLUMN sector TEXT NOT NULL DEFAULT 'general'"),
            ("message_id", "ALTER TABLE sends ADD COLUMN message_id TEXT"),
            ("smtp_response_code", "ALTER TABLE sends ADD COLUMN smtp_response_code INTEGER"),
            ("smtp_response_message", "ALTER TABLE sends ADD COLUMN smtp_response_message TEXT"),
        ]
        for col, sql in migrations:
            if col not in cols:
                conn.execute(sql)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sends_timestamp ON sends(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sends_status ON sends(status)")
        conn.commit()
    finally:
        conn.close()


def init_email_templates_table(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS email_templates (
                sector TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                html TEXT NOT NULL,
                text TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def init_leads_table(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS leads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sector_id TEXT NOT NULL,
                email TEXT NOT NULL,
                first_name TEXT,
                company TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_sector ON leads(sector_id)")
        conn.commit()
    finally:
        conn.close()


def get_leads(sector, limit=None, db_path=DB_PATH):
    init_leads_table(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        q = "SELECT email, first_name, company FROM leads WHERE sector_id=?"
        params = (sector,)
        if limit is not None:
            q += " LIMIT ?"
            params = (sector, int(limit))
        rows = conn.execute(q, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def seed_email_templates(db_path=DB_PATH):
    init_email_templates_table(db_path)
    conn = sqlite3.connect(db_path)
    try:
        for sector in SECTORS:
            cur = conn.execute("SELECT COUNT(*) FROM email_templates WHERE sector=?", (sector,))
            if cur.fetchone()[0] == 0:
                html_path = f"email_template_{sector}.html"
                txt_path = f"email_template_{sector}.txt"
                html = Path(html_path).read_text(encoding="utf-8") if Path(html_path).exists() else ""
                text = Path(txt_path).read_text(encoding="utf-8") if Path(txt_path).exists() else ""
                subject = get_env(f"EMAIL_SUBJECT_{sector.upper()}", default=get_env("EMAIL_SUBJECT", default="{{first_name}}, is PPE supply a risk on your projects?"))
                conn.execute(
                    "INSERT INTO email_templates (sector, subject, html, text) VALUES (?, ?, ?, ?)",
                    (sector, subject, html, text),
                )
        conn.commit()
    finally:
        conn.close()


def get_email_template(sector, db_path=DB_PATH):
    seed_email_templates(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT subject, html, text FROM email_templates WHERE sector=?", (sector,)).fetchone()
    finally:
        conn.close()
    if row:
        return {"subject": row["subject"], "html": row["html"], "text": row["text"]}
    return {"subject": "", "html": "", "text": ""}


def save_email_template(sector, subject, html, text, db_path=DB_PATH):
    init_email_templates_table(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO email_templates (sector, subject, html, text, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(sector) DO UPDATE SET
                subject=excluded.subject, html=excluded.html, text=excluded.text, updated_at=CURRENT_TIMESTAMP
            """,
            (sector, subject, html, text),
        )
        conn.commit()
    finally:
        conn.close()


def record_result(
    lead_email,
    first_name,
    company,
    status,
    sector="general",
    error_message="",
    subject="",
    dry_run=False,
    message_id="",
    smtp_response_code=None,
    smtp_response_message="",
    log_path=LOG_FILE_PATH,
    db_path=DB_PATH,
):
    """Record a send result to both the CSV log and the SQLite database."""
    timestamp = datetime.now().isoformat()

    # CSV log (kept for backwards compatibility / simple viewing).
    p = Path(log_path)
    file_exists = p.exists() and p.stat().st_size > 0
    with open(log_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(
                ["timestamp", "lead_email", "first_name", "status", "error_message"]
            )
        writer.writerow([timestamp, lead_email, first_name, status, error_message])

    # SQLite metrics store for the dashboard.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO sends
                (timestamp, sector, lead_email, first_name, company, status, error_message,
                 subject, dry_run, smtp_server, message_id, smtp_response_code,
                 smtp_response_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                sector,
                lead_email,
                first_name,
                company,
                status,
                error_message,
                subject,
                1 if dry_run else 0,
                SMTP_SERVER,
                message_id,
                smtp_response_code,
                smtp_response_message,
            ),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main processing loop
# ---------------------------------------------------------------------------


def process_leads(sector=None, limit=None, dry_run_override=None):
    global CURRENT_SUBJECT

    global DRY_RUN
    if sector is None:
        sector = os.getenv("SECTOR", "general")
    cfg = sector_config(sector)
    sector = cfg["sector"]
    CURRENT_SUBJECT = cfg["subject"]

    if dry_run_override is not None:
        DRY_RUN = bool(dry_run_override)

    tpl = get_email_template(sector)
    CURRENT_SUBJECT = tpl["subject"] or cfg["subject"]
    html_template = tpl["html"]
    text_template = tpl["text"]

    init_leads_table()
    leads = get_leads(sector, limit=limit)

    if limit is not None:
        print(f"Limiting to first {limit} lead(s).")

    print(f"Sector: {sector}")
    print(f"Loaded {len(leads)} lead(s) from leads table.")
    print(f"DRY_RUN = {DRY_RUN}")

    init_db()
    seed_email_templates()
    print(f"Metrics database: {DB_PATH}")

    # Already-contacted emails from DB (across all sectors).
    conn = sqlite3.connect(DB_PATH)
    try:
        contacted_emails = {r[0].lower() for r in conn.execute(
            "SELECT lead_email FROM sends WHERE status='SUCCESS' AND dry_run=0"
        ).fetchall()}
    finally:
        conn.close()

    seen_emails = set()

    server = None
    if not DRY_RUN:
        try:
            server = connect_smtp()
            print(f"Connected to SMTP server {SMTP_SERVER}:{SMTP_PORT}")
        except Exception as exc:
            print(f"[ERROR] Could not connect to SMTP server: {exc}")
            raise

    try:
        total = len(leads)
        for i, row in enumerate(leads):
            context = build_context(row, survey_url=cfg["survey_url"])
            email = (context.get("email") or "").strip()
            first_name = context.get("first_name", "there")
            company = context.get("company", "")
            subject = render_template(CURRENT_SUBJECT, context)
            email_lower = email.lower()
            whitelisted = email_lower in WHITELIST_EMAILS

            if not whitelisted:
                if email_lower in contacted_emails or email_lower in seen_emails:
                    print(f"[SKIP] Already contacted / duplicate email: {email}")
                    record_result(email, first_name, company, "SKIPPED",
                                 sector=sector, error_message="Skipped: already contacted or duplicate email",
                                 subject=subject, dry_run=DRY_RUN)
                    continue

            if not validate_email(email):
                print(f"[WARN] Skipping invalid email: {email!r}")
                record_result(email, first_name, company, "FAILED", sector=sector,
                             error_message="Invalid email format", subject=subject, dry_run=DRY_RUN)
                continue

            message_id = make_msgid(domain=SENDER_EMAIL.split("@")[-1])
            msg, message_id = build_message(email, context, html_template, text_template,
                                            cfg["html_path"], message_id=message_id)

            if DRY_RUN:
                print(f"[DRY RUN] Would send to {email} ({first_name})")
                record_result(email, first_name, company, "SUCCESS", sector=sector,
                             error_message="Dry run - not sent", subject=subject, dry_run=True,
                             message_id=message_id)
            else:
                smtp_code = None
                smtp_msg = ""
                try:
                    server.mail(SENDER_EMAIL)
                    server.rcpt(email)
                    code, resp = server.data(msg.as_string())
                    if isinstance(resp, (bytes, bytearray)):
                        resp = resp.decode("utf-8", "replace")
                    smtp_code = code
                    smtp_msg = resp
                    if code // 100 != 2:
                        raise smtplib.SMTPDataError(code, resp)
                    print(f"[SUCCESS] Sent to {email} (SMTP {code})")
                    record_result(email, first_name, company, "SUCCESS", sector=sector,
                                 subject=subject, dry_run=DRY_RUN,
                                 message_id=message_id, smtp_response_code=smtp_code,
                                 smtp_response_message=smtp_msg)
                except Exception as exc:
                    err = str(exc)
                    if smtp_code is None:
                        smtp_code = getattr(exc, "smtp_code", None)
                        smtp_msg = getattr(exc, "smtp_error", err)
                        if isinstance(smtp_msg, (bytes, bytearray)):
                            smtp_msg = smtp_msg.decode("utf-8", "replace")
                    print(f"[FAILED] {email}: {err}")
                    record_result(email, first_name, company, "FAILED", sector=sector,
                                 error_message=err, subject=subject, dry_run=DRY_RUN,
                                 message_id=message_id, smtp_response_code=smtp_code,
                                 smtp_response_message=smtp_msg)

                    safe_quit(server)
                    server = None
                    try:
                        server = connect_smtp()
                        print("[INFO] Reconnected to SMTP server.")
                    except Exception as re_err:
                        print(f"[ERROR] Reconnect failed: {re_err}")
                        break

            if not whitelisted:
                seen_emails.add(email_lower)

            if i < total - 1:
                delay = random.uniform(2.0, 5.0)
                print(f"[INFO] Waiting {delay:.2f}s before next send...")
                time.sleep(delay)
    finally:
        safe_quit(server)
        print("SMTP session closed.")


def parse_args():
    parser = argparse.ArgumentParser(description="Send personalized email campaign.")
    parser.add_argument("--sector", choices=SECTORS, default=os.getenv("SECTOR", "general"),
                        help="Sector to send for (general/agriculture/hospitality).")
    parser.add_argument("--limit", type=int, default=None, help="Max number of leads to process.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--live", action="store_true", help="Force live send (overrides DRY_RUN).")
    group.add_argument("--dry-run", dest="dry_run", action="store_true", help="Force dry run (overrides DRY_RUN).")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        args = parse_args()
        override = None
        if args.live:
            override = False
        elif args.dry_run:
            override = True
        process_leads(args.sector, limit=args.limit, dry_run_override=override)
    except KeyboardInterrupt:
        print("\n[INFO] Interrupted by user.")
    except Exception as exc:
        print(f"[FATAL] {exc}")
        raise
