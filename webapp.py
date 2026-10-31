"""FastAPI web admin panel (Tailwind + DataTables). Run: uvicorn webapp:app --reload --port 8000"""

import os
import subprocess
import sys
from datetime import datetime

import pandas as pd
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

import admin_common as ac
import send_campaign as sc

SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production")
serializer = URLSafeTimedSerializer(SECRET_KEY)

app = FastAPI(title="Frontline Safety Fear [PPE Scorecard]")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

def get_sectors():
    return sc.get_sectors()


def get_sector_options():
    opts = []
    for name in sc.get_sectors():
        sid = sc.get_sector_id(name)
        opts.append({"id": sid, "name": name})
    return opts

def list_remote_images():
    import paramiko
    images = []
    try:
        transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
        transport.connect(username=SFTP_USERNAME, password=SFTP_PASSWORD)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_dir = SFTP_REMOTE_DIR if SFTP_REMOTE_DIR not in ('', '.') else '.'
        for attr in sftp.listdir_attr(remote_dir):
            name = attr.filename
            if name.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
                images.append({"name": name, "url": f"https://frontline-footwear.com/scoreapp/{name}"})
        sftp.close()
        transport.close()
    except Exception:
        pass
    return sorted(images, key=lambda x: x["name"])

SFTP_HOST = os.getenv('SFTP_HOST', 'www43.jnb2.host-h.net')
SFTP_PORT = int(os.getenv('SFTP_PORT', '22'))
SFTP_USERNAME = os.getenv('SFTP_USERNAME', 'frontjsfcc_1')
SFTP_PASSWORD = os.getenv('SFTP_PASSWORD', '41746U5U542a9x$8')
SFTP_REMOTE_DIR = os.getenv('SFTP_REMOTE_DIR', '.')
sc.seed_email_templates()


def get_user(request: Request):
    cookie = request.cookies.get("session")
    if not cookie:
        return None
    try:
        data = serializer.loads(cookie, max_age=60 * 60 * 8)
        return data.get("email")
    except (BadSignature, SignatureExpired):
        return None


def require_user(request: Request):
    user = get_user(request)
    if not user:
        return None
    return user


def render(request, template, **ctx):
    user = get_user(request)
    ctx.setdefault("user", user)
    ctx.setdefault("flash", request.query_params.get("flash"))
    return templates.TemplateResponse(request, template, {"request": request, **ctx})


@app.get("/", response_class=RedirectResponse)
def root():
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    if get_user(request):
        return RedirectResponse(url="/dashboard", status_code=302)
    return templates.TemplateResponse(request, "login.html", {"error": None, "info": None, "reset_token": None})


@app.post("/login", response_class=HTMLResponse)
def login_post(request: Request, email: str = Form(...), password: str = Form(...)):
    ac.seed_admin()
    name = ac.authenticate(email, password)
    if name:
        token = serializer.dumps({"email": email, "name": name})
        resp = RedirectResponse(url="/dashboard", status_code=303)
        resp.set_cookie("session", token, httponly=True, max_age=60 * 60 * 8)
        return resp
    return templates.TemplateResponse(request, "login.html", {"error": "Invalid email or password.", "info": None, "reset_token": None})


@app.get("/logout", response_class=RedirectResponse)
def logout():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("session")
    return resp


@app.post("/reset-request", response_class=HTMLResponse)
def reset_request(request: Request, email: str = Form(...)):
    token = ac.request_password_reset(email)
    info = "If the account exists, a reset token was generated." if token else "If the account exists, a reset token was generated."
    return templates.TemplateResponse(request, "login.html", {"error": None, "info": info, "reset_token": token})


@app.post("/reset-confirm", response_class=HTMLResponse)
def reset_confirm(request: Request, token: str = Form(...), new_password: str = Form(...)):
    ok = ac.reset_password(token, new_password)
    if ok:
        return templates.TemplateResponse(request, "login.html", {"error": None, "info": "Password updated. Please log in.", "reset_token": None})
    return templates.TemplateResponse(request, "login.html", {"error": "Invalid or expired token.", "info": None, "reset_token": None})


def auth_guard(request: Request):
    user = get_user(request)
    if not user:
        return None
    return user


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    df = ac.load_data()
    rows = []
    total = success = failed = skipped = live = 0
    if not df.empty:
        total = len(df)
        success = int((df["status"] == "SUCCESS").sum())
        failed = int((df["status"] == "FAILED").sum())
        skipped = int((df["status"] == "SKIPPED").sum())
        live = int((df["dry_run"] == 0).sum())
        rows = df.to_dict("records")
    return render(request, "metrics.html", active="dashboard", user=user,
                  total=total, success=success, failed=failed, skipped=skipped, live=live, rows=rows)


@app.get("/metrics", response_class=RedirectResponse)
def metrics_redirect():
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/export/csv")
def export_csv(request: Request):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    df = ac.load_data()
    if df.empty:
        return Response(content="", media_type="text/csv")
    csv_data = df.to_csv(index=False)
    return Response(content=csv_data, media_type="text/csv",
                    headers={"Content-Disposition": "attachment; filename=sends_export.csv"})


@app.get("/send", response_class=HTMLResponse)
def send_get(request: Request):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    _opts = get_sector_options()
    _default = str(_opts[0]["id"]) if _opts else "1"
    preview_sector = request.query_params.get("preview_sector", _default)
    preview_idx = int(request.query_params.get("preview_idx", 0))
    preview_subject = None
    preview_html = None
    return render(request, "send.html", active="send", user=user, sector_options=get_sector_options(),
                  preview_sector=preview_sector, preview_idx=preview_idx,
                  preview_subject=preview_subject, preview_html=preview_html, output=None, error=None)


@app.post("/send", response_class=HTMLResponse)
def send_post(request: Request, sector: str = Form(...), volume: int = Form(10),
              dry_run: bool = Form(False), live: bool = Form(False)):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    cmd = [sys.executable, "send_campaign.py", "--sector", sector, "--limit", str(volume)]
    if live:
        cmd.append("--live")
    elif dry_run:
        cmd.append("--dry-run")
    import subprocess
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
    output = proc.stdout
    error = proc.stderr if proc.returncode != 0 else None
    if proc.returncode != 0 and not error:
        error = proc.stderr
    # reload preview defaults
    return render(request, "send.html", active="send", user=user, sector_options=get_sector_options(),
                  preview_sector=sector, preview_idx=0, preview_subject=None, preview_html=None,
                  output=output, error=error)


@app.get("/import", response_class=HTMLResponse)
def import_get(request: Request):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return render(request, "import.html", active="import", user=user, sector_options=get_sector_options(), message=None, error=None, preview=None, preview_cols=None, preview_total=0)


def _normalize_header(h):
    return "".join(c if c.isalnum() else "_" for c in str(h).lower()).strip("_")


COLUMN_MAP = {
    "first_name": ["first_name", "first name", "firstname", "first"],
    "last_name": ["last_name", "last name", "lastname", "last"],
    "full_name": ["full_name", "full name", "fullname", "name", "full name "],
    "job_title": ["job_title", "job title", "jobtitle", "title", "position", "role"],
    "company": ["company", "company name", "organization", "organisation", "employer"],
    "industry": ["industry", "sector"],
    "department": ["department", "dept"],
    "company_size": ["company_size", "company size", "companysize", "size", "employees"],
    "company_type": ["company_type", "company type", "companytype", "type"],
    "email": ["email", "email address", "e_mail", "mail", "email_address", "emailaddress"],
    "linkedin_url": ["linkedin_url", "linkedin url", "linkedin", "linkedin profile", "linkedin_url"],
    "phone_number": ["phone_number", "phone number", "phone", "telephone", "mobile", "phone no"],
}


def _map_columns(df):
    norm = {_normalize_header(c): c for c in df.columns}
    mapping = {}
    for target, candidates in COLUMN_MAP.items():
        for cand in candidates:
            key = _normalize_header(cand)
            if key in norm:
                mapping[target] = norm[key]
                break
    return mapping


@app.post("/import", response_class=HTMLResponse)
def import_post(request: Request, sector: int = Form(...), file: UploadFile = File(...)):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    import io
    content = file.file.read()
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as e:
        return render(request, "import.html", active="import", user=user, sector_options=get_sector_options(),
                      message=None, error=f"Failed to read CSV: {e}", preview=None, preview_cols=None, preview_total=0)
    colmap = _map_columns(df)
    if "email" not in colmap:
        return render(request, "import.html", active="import", user=user, sector_options=get_sector_options(),
                      message=None, error="CSV must contain an email column (e.g. 'Email address' or 'email').",
                      preview=df.head(20).to_dict("records"), preview_cols=list(df.columns), preview_total=len(df))
    import sqlite3
    import send_campaign as sc
    conn = sqlite3.connect(sc.DB_PATH)
    try:
        sc.init_leads_table(sc.DB_PATH)
        inserted = 0
        skipped = 0
        for _, r in df.iterrows():
            email = str(r.get(colmap["email"], "")).strip()
            if not email:
                continue
            cur = conn.execute("SELECT COUNT(*) FROM leads WHERE sector_id=? AND email=?", (sector, email))
            if cur.fetchone()[0] > 0:
                skipped += 1
                continue
            vals = {}
            for target in COLUMN_MAP:
                if target in colmap:
                    v = r.get(colmap[target])
                    vals[target] = "" if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v).strip()
                else:
                    vals[target] = ""
            # first_name fallback from full_name
            if not vals.get("first_name") and vals.get("full_name"):
                vals["first_name"] = vals["full_name"].split()[0] if vals["full_name"].split() else ""
            conn.execute(
                """INSERT INTO leads (sector_id, first_name, last_name, full_name, job_title, company,
                   industry, department, company_size, company_type, email, linkedin_url, phone_number, sent)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
                (sector, vals.get("first_name", ""), vals.get("last_name", ""), vals.get("full_name", ""),
                 vals.get("job_title", ""), vals.get("company", ""), vals.get("industry", ""),
                 vals.get("department", ""), vals.get("company_size", ""), vals.get("company_type", ""),
                 email, vals.get("linkedin_url", ""), vals.get("phone_number", "")),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return render(request, "import.html", active="import", user=user, sector_options=get_sector_options(),
                  message=f"Imported {inserted} new leads (skipped {skipped} existing).", error=None,
                  preview=df.head(20).to_dict("records"), preview_cols=list(df.columns), preview_total=len(df))


@app.get("/emails", response_class=HTMLResponse)
def emails_list(request: Request):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    templates_list = []
    for s in get_sectors():
        t = sc.get_email_template(s)
        templates_list.append({"sector": s, "subject": t["subject"]})
    return render(request, "emails.html", active="emails", user=user, templates=templates_list)


@app.get("/emails/view/{sector}", response_class=HTMLResponse)
def email_view(request: Request, sector: str):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    t = sc.get_email_template(sector)
    # inline images
    import base64, mimetypes, re
    html = t["html"]
    cfg = sc.sector_config(sector)
    def _inline(match):
        src = match.group(2)
        if src.startswith(("http://", "https://", "data:", "cid:")):
            return match.group(0)
        p = None
        for cand in (os.path.join(os.path.dirname(cfg["html_path"]), src), src, os.path.join(os.getcwd(), src)):
            if os.path.isfile(cand):
                p = cand
                break
        if not p:
            return match.group(0)
        mime = mimetypes.guess_type(p)[0] or "application/octet-stream"
        data = base64.b64encode(open(p, "rb").read()).decode("ascii")
        return f'{match.group(1)}data:{mime};base64,{data}{match.group(3)}'
    html_inlined = re.sub(r'(src\s*=\s*["\'])([^"\']+)(["\'])', _inline, html, flags=re.IGNORECASE)
    return render(request, "email_view.html", active="emails", user=user, sector=sector,
                  subject=t["subject"], html=html_inlined)


@app.get("/emails/edit/{sector}", response_class=HTMLResponse)
def email_edit_get(request: Request, sector: str):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    t = sc.get_email_template(sector)
    return render(request, "email_edit.html", active="emails", user=user, sector=sector,
                  subject=t["subject"], html=t["html"], text=t["text"], message=None, error=None)


@app.post("/emails/edit/{sector}", response_class=HTMLResponse)
def email_edit_post(request: Request, sector: str, subject: str = Form(...), html: str = Form(...), text: str = Form(...)):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    try:
        sc.save_email_template(sector, subject, html, text)
        msg = "Saved."
    except Exception as e:
        msg = None
        err = str(e)
        t = sc.get_email_template(sector)
        return render(request, "email_edit.html", active="emails", user=user, sector=sector,
                      subject=subject, html=html, text=text, message=None, error=err)
    t = sc.get_email_template(sector)
    return render(request, "email_edit.html", active="emails", user=user, sector=sector,
                  subject=t["subject"], html=t["html"], text=t["text"], message=msg, error=None)



@app.get("/sectors", response_class=HTMLResponse)
def sectors(request: Request):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    import sqlite3, send_campaign as sc
    sc.init_leads_table(sc.DB_PATH)
    sectors_info = []
    conn = sqlite3.connect(sc.DB_PATH)
    try:
        for name in sc.get_sectors():
            sid = sc.get_sector_id(name, sc.DB_PATH)
            sent = conn.execute("SELECT COUNT(*) FROM sends WHERE sector_id=? AND status='SUCCESS' AND dry_run=0", (sid,)).fetchone()[0]
            total_leads = conn.execute("SELECT COUNT(*) FROM leads WHERE sector_id=?", (sid,)).fetchone()[0]
            unsent = conn.execute("SELECT COUNT(*) FROM leads WHERE sector_id=? AND email NOT IN (SELECT lead_email FROM sends WHERE sector_id=? AND status='SUCCESS' AND dry_run=0)", (sid, sid)).fetchone()[0]
            sectors_info.append({"slug": name, "name": name, "sent": sent, "total_leads": total_leads, "unsent": unsent})
    finally:
        conn.close()
    return render(request, "sectors.html", active="sectors", user=user, sectors_info=sectors_info, message=None, error=None)


@app.post("/sectors/add", response_class=HTMLResponse)
def sectors_add(request: Request, name: str = Form(...)):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    try:
        sc.add_sector(name)
        msg = f"Sector '{name}' added."
        err = None
    except Exception as e:
        msg = None
        err = str(e)
    sectors_info = _sectors_info()
    return render(request, "sectors.html", active="sectors", user=user, sectors_info=sectors_info, message=msg, error=err)


@app.post("/sectors/delete/{slug}", response_class=HTMLResponse)
def sectors_delete(request: Request, slug: str):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    try:
        sc.delete_sector(slug)
        msg = f"Sector '{slug}' deleted."
        err = None
    except Exception as e:
        msg = None
        err = str(e)
    sectors_info = _sectors_info()
    return render(request, "sectors.html", active="sectors", user=user, sectors_info=sectors_info, message=msg, error=err)


def _sectors_info():
    import sqlite3, send_campaign as sc
    sc.init_leads_table(sc.DB_PATH)
    sectors_info = []
    conn = sqlite3.connect(sc.DB_PATH)
    try:
        for name in sc.get_sectors():
            sid = sc.get_sector_id(name, sc.DB_PATH)
            sent = conn.execute("SELECT COUNT(*) FROM sends WHERE sector_id=? AND status='SUCCESS' AND dry_run=0", (sid,)).fetchone()[0]
            total_leads = conn.execute("SELECT COUNT(*) FROM leads WHERE sector_id=?", (sid,)).fetchone()[0]
            unsent = conn.execute("SELECT COUNT(*) FROM leads WHERE sector_id=? AND email NOT IN (SELECT lead_email FROM sends WHERE sector_id=? AND status='SUCCESS' AND dry_run=0)", (sid, sid)).fetchone()[0]
            sectors_info.append({"slug": name, "name": name, "sent": sent, "total_leads": total_leads, "unsent": unsent})
    finally:
        conn.close()
    return sectors_info


@app.post("/media/upload", response_class=HTMLResponse)
def media_upload(request: Request, file: UploadFile = File(...)):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    import ftplib, io
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        templates_list = [{"sector": s, "subject": sc.get_email_template(s)["subject"]} for s in get_sectors()]
        return render(request, "media.html", active="media", user=user,
                      upload_error="Only .jpg and .png files are allowed.", upload_url=None,
                      images=list_remote_images())
    try:
        data = file.file.read()
        import paramiko
        transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
        transport.connect(username=SFTP_USERNAME, password=SFTP_PASSWORD)
        sftp = paramiko.SFTPClient.from_transport(transport)
        remote_path = filename if SFTP_REMOTE_DIR in ('', '.', '/') else f"{SFTP_REMOTE_DIR.rstrip('/')}/{filename}"
        bio = io.BytesIO(data)
        sftp.putfo(bio, remote_path)
        sftp.close()
        transport.close()
        url = f"https://frontline-footwear.com/scoreapp/{filename}"
        templates_list = [{"sector": s, "subject": sc.get_email_template(s)["subject"]} for s in get_sectors()]
        return render(request, "media.html", active="media", user=user,
                      upload_error=None, upload_url=url,
                      images=list_remote_images())
    except Exception as e:
        templates_list = [{"sector": s, "subject": sc.get_email_template(s)["subject"]} for s in get_sectors()]
        return render(request, "media.html", active="media", user=user,
                      upload_error=f"Upload failed: {e}", upload_url=None,
                      images=list_remote_images())




@app.get("/sectors/{sector}/leads", response_class=HTMLResponse)
def sector_leads(request: Request, sector: str):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    import sqlite3, send_campaign as sc
    sid = sc.get_sector_id(sector)
    if sid is None:
        return RedirectResponse(url="/sectors", status_code=303)
    conn = sqlite3.connect(sc.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM leads WHERE sector_id=? ORDER BY id", (sid,)).fetchall()]
        name_row = conn.execute("SELECT name FROM sectors WHERE id=?", (sid,)).fetchone()
        total_leads = conn.execute("SELECT COUNT(*) FROM leads WHERE sector_id=?", (sid,)).fetchone()[0]
        sent = conn.execute("SELECT COUNT(*) FROM leads WHERE sector_id=? AND sent=1", (sid,)).fetchone()[0]
    finally:
        conn.close()
    sector_name = name_row["name"] if name_row else sector
    available = total_leads - sent
    return render(request, "sector_leads.html", active="sectors", user=user, sector_name=sector_name,
                  leads=rows, total_leads=total_leads, sent=sent, available=available)


@app.get("/media", response_class=HTMLResponse)
def media_page(request: Request):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return render(request, "media.html", active="media", user=user, images=list_remote_images(),
                  upload_error=None, upload_url=None)



@app.get("/deploy", response_class=HTMLResponse)
def deploy(request: Request):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return render(request, "deploy.html", active="deploy", user=user)


