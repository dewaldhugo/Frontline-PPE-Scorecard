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

SECTORS = ac.SECTORS

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
    preview_sector = request.query_params.get("preview_sector", "general")
    preview_idx = int(request.query_params.get("preview_idx", 0))
    preview_subject = None
    preview_html = None
    try:
        import send_campaign as sc
        import base64, mimetypes, re
        import pandas as pd
        leads_path = f"leads_{preview_sector}.csv"
        if os.path.exists(leads_path):
            pdf = pd.read_csv(leads_path)
            if len(pdf) > 0:
                idx = min(max(preview_idx, 0), len(pdf) - 1)
                row = pdf.iloc[idx].to_dict()
                cfg = sc.sector_config(preview_sector)
                ctx = sc.build_context(row, survey_url=cfg["survey_url"])
                preview_subject = sc.render_template(cfg["subject"], ctx)
                html = sc.render_template(sc.load_template(cfg["html_path"]), ctx)

                def _inline(match):
                    src = match.group(2)
                    if src.startswith(("http://", "https://", "data:", "cid:")):
                        return match.group(0)
                    # resolve file
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

                preview_html = re.sub(r'(src\s*=\s*["\'])([^"\']+)(["\'])', _inline, html, flags=re.IGNORECASE)
    except Exception as e:
        preview_subject = f"Error: {e}"
    return render(request, "send.html", active="send", user=user, sectors=SECTORS,
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
    return render(request, "send.html", active="send", user=user, sectors=SECTORS,
                  preview_sector=sector, preview_idx=0, preview_subject=None, preview_html=None,
                  output=output, error=error)


@app.get("/import", response_class=HTMLResponse)
def import_get(request: Request):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    return render(request, "import.html", active="import", user=user, sectors=SECTORS, message=None, error=None, preview=None, preview_cols=None, preview_total=0)


@app.post("/import", response_class=HTMLResponse)
def import_post(request: Request, sector: str = Form(...), mode: str = Form(...), file: UploadFile = File(...)):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    import io
    content = file.file.read()
    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as e:
        return render(request, "import.html", active="import", user=user, sectors=SECTORS,
                      message=None, error=f"Failed to read CSV: {e}", preview=None, preview_cols=None, preview_total=0)
    if "email" not in df.columns or "first_name" not in df.columns:
        return render(request, "import.html", active="import", user=user, sectors=SECTORS,
                      message=None, error="CSV must contain 'email' and 'first_name' columns.",
                      preview=df.head(20).to_dict("records"), preview_cols=list(df.columns), preview_total=len(df))
    import sqlite3
    conn = sqlite3.connect(ac.DB_PATH if hasattr(ac,'DB_PATH') else __import__('os').getenv('DB_PATH','campaign.db'))
    # use send_campaign DB path
    import send_campaign as sc
    conn = sqlite3.connect(sc.DB_PATH)
    try:
        sc.init_leads_table(sc.DB_PATH)
        inserted = 0
        for _, r in df.iterrows():
            email = str(r.get("email", "")).strip()
            if not email:
                continue
            if mode == "append":
                cur = conn.execute("SELECT COUNT(*) FROM leads WHERE sector_id=? AND email=?", (sector, email))
                if cur.fetchone()[0] > 0:
                    continue
            conn.execute(
                "INSERT INTO leads (sector_id, email, first_name, company) VALUES (?,?,?,?)",
                (sector, email, str(r.get("first_name", "")).strip(), str(r.get("company", "")).strip()),
            )
            inserted += 1
        conn.commit()
    finally:
        conn.close()
    return render(request, "import.html", active="import", user=user, sectors=SECTORS,
                  message=f"Imported {inserted} new leads into sector '{sector}'.", error=None,
                  preview=df.head(20).to_dict("records"), preview_cols=list(df.columns), preview_total=len(df))



@app.get("/emails", response_class=HTMLResponse)
def emails_list(request: Request):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    templates_list = []
    for s in SECTORS:
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
        for slug in SECTORS:
            sent = conn.execute(
                "SELECT COUNT(DISTINCT lead_email) FROM sends WHERE sector=? AND status='SUCCESS' AND dry_run=0", (slug,)
            ).fetchone()[0]
            total_leads = conn.execute("SELECT COUNT(*) FROM leads WHERE sector_id=?", (slug,)).fetchone()[0]
            sent_emails = {r[0] for r in conn.execute(
                "SELECT lead_email FROM sends WHERE sector=? AND status='SUCCESS' AND dry_run=0", (slug,)).fetchall()}
            unsent = conn.execute("SELECT COUNT(*) FROM leads WHERE sector_id=? AND email NOT IN (SELECT lead_email FROM sends WHERE status='SUCCESS' AND dry_run=0)", (slug,)).fetchone()[0]
            sectors_info.append({"slug": slug, "sent": sent, "total_leads": total_leads, "unsent": unsent})
    finally:
        conn.close()
    return render(request, "sectors.html", active="sectors", user=user, sectors_info=sectors_info)



@app.post("/media/upload", response_class=HTMLResponse)
def media_upload(request: Request, file: UploadFile = File(...)):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    import ftplib, io
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1].lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        templates_list = [{"sector": s, "subject": sc.get_email_template(s)["subject"]} for s in SECTORS]
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
        templates_list = [{"sector": s, "subject": sc.get_email_template(s)["subject"]} for s in SECTORS]
        return render(request, "media.html", active="media", user=user,
                      upload_error=None, upload_url=url,
                      images=list_remote_images())
    except Exception as e:
        templates_list = [{"sector": s, "subject": sc.get_email_template(s)["subject"]} for s in SECTORS]
        return render(request, "media.html", active="media", user=user,
                      upload_error=f"Upload failed: {e}", upload_url=None,
                      images=list_remote_images())



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


@app.get("/config", response_class=HTMLResponse)
def config(request: Request):
    user = auth_guard(request)
    if not user:
        return RedirectResponse(url="/login", status_code=303)
    env_content = open(".env").read() if os.path.exists(".env") else "No .env file found."
    return render(request, "config.html", active="config", user=user, env_content=env_content)
