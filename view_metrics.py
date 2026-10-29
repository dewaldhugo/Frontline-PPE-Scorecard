#!/usr/bin/env python3
"""Simple CLI viewer for send metrics stored in SQLite (campaign.db)."""

import os
import argparse
import sqlite3
import sys
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "data/campaign.db")


def main(sector=None):
    if not os.path.exists(DB_PATH):
        print(f"No metrics database found at {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        where = ""
        params = ()
        if sector:
            where = "WHERE sector=?"
            params = (sector,)

        total = conn.execute(f"SELECT COUNT(*) FROM sends {where}", params).fetchone()[0]
        success = conn.execute(f"SELECT COUNT(*) FROM sends {where}{' AND ' if where else 'WHERE '}status='SUCCESS'", params).fetchone()[0]
        failed = conn.execute(f"SELECT COUNT(*) FROM sends {where}{' AND ' if where else 'WHERE '}status='FAILED'", params).fetchone()[0]
        skipped = conn.execute(f"SELECT COUNT(*) FROM sends {where}{' AND ' if where else 'WHERE '}status='SKIPPED'", params).fetchone()[0]
        dry = conn.execute(f"SELECT COUNT(*) FROM sends {where}{' AND ' if where else 'WHERE '}dry_run=1", params).fetchone()[0]
        live = total - dry

        title = f" ({sector})" if sector else ""
        print(f"=== Send Metrics Summary{title} ===")
        print(f"Total records : {total}")
        print(f"Success       : {success}")
        print(f"Failed        : {failed}")
        print(f"Skipped       : {skipped}")
        print(f"Dry runs      : {dry}")
        print(f"Live sends    : {live}")
        print()

        print("=== Recent sends (last 20) ===")
        q = "SELECT timestamp, lead_email, first_name, company, status, dry_run, sector, error_message FROM sends"
        if sector:
            q += " WHERE sector=?"
        q += " ORDER BY id DESC LIMIT 20"
        rows = conn.execute(q, params).fetchall()
        for r in rows:
            tag = "DRY" if r["dry_run"] else "LIVE"
            err = f"  ({r['error_message']})" if r["error_message"] else ""
            print(f"[{tag}][{r['sector']}] {r['timestamp']}  {r['status']:7}  {r['lead_email']}  "
                  f"{r['first_name']} @ {r['company']}{err}")
    finally:
        conn.close()


def parse_args():
    parser = argparse.ArgumentParser(description="View send metrics.")
    parser.add_argument("--sector", choices=("general", "agriculture", "hospitality"), default=None)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    main(args.sector)