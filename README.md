# Frontline PPE Scorecard — Email Campaign Sender

Production-ready Python application for sending personalized multipart
(plain-text + HTML) emails over SMTP, with dry-run support, rate limiting,
global deduplication across sectors, and a Tailwind/DataTables admin panel.

## Directory structure

```
.
├── webapp.py              # FastAPI admin panel (Tailwind + DataTables)
├── send_campaign.py       # CLI sender
├── admin_common.py        # Shared DB/auth helpers
├── view_metrics.py        # CLI metrics viewer
├── templates/             # Jinja2 templates
├── static/                # Static assets (logo)
├── data/campaign.db       # SQLite database (leads, templates, sends, users, sectors)
├── logs/campaign_log.csv  # Per-lead results log
├── .env                   # Configuration (not committed)
├── .env.example           # Configuration template
└── .gitignore
```

## Setup

1. Create and activate a virtual environment (optional):

   ```bash
   python -m venv .venv
   # Windows
   .venv\Scripts\activate
   # macOS / Linux
   source .venv/bin/activate
   ```

2. Install dependencies:

   ```bash
   pip install python-dotenv bcrypt fastapi jinja2 python-multipart itsdangerous uvicorn httpx paramiko
   ```

3. Configure environment:

   ```bash
   # Windows
   copy .env.example .env
   # macOS / Linux
   cp .env.example .env
   ```

   Edit `.env` with SMTP credentials, sender details, ScoreApp survey URLs, and
   optional SFTP settings.

4. Seed data is created automatically on first run (sectors, leads, email
   templates, admin user).

## Usage

### Dry run (recommended first)

```bash
python send_campaign.py --sector general --dry-run
```

### Real send

```bash
python send_campaign.py --sector general --live
```

Sectors: `general`, `agriculture`, `hospitality`. Deduplication is global across
all sectors: no recipient (except whitelisted test addresses) is contacted twice.

CLI options:

- `--sector` — target sector (default: `general`).
- `--limit N` — process at most N leads.
- `--live` — force a real send (overrides `DRY_RUN`).
- `--dry-run` — force a dry run (overrides `DRY_RUN`).

### Admin panel

```bash
uvicorn webapp:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000 and log in with `dev@frontlinefootwear.com` /
`Success@27!`.

Features: Dashboard (metrics + DataTables), Sectors, Emails (view/edit, image
upload via SFTP), Send Campaign (preview, progress, cancel), Import Leads, Config.

### Metrics (CLI)

```bash
python view_metrics.py
python view_metrics.py --sector agriculture
```

## Notes

- A 1 second delay is applied between sends.
- Invalid email addresses are skipped with a warning and logged as FAILED.
- The SMTP session is reused across sends and closed safely on exit.
- Press Ctrl+C to stop; the session is closed gracefully.
- Images are hosted publicly at https://frontline-footwear.com/scoreapp/.
- `data/campaign.db` and `logs/campaign_log.csv` are gitignored (local data).
- `.env` is gitignored; use `.env.example` as a template.