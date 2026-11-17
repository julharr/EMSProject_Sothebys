"""Local copy of The Met's Open Access dataset (github.com/metmuseum/openaccess, CC0).

MetObjects.csv lists ~470,000 objects with their credit lines ("Gift of Jane Roe, 2019") and
departments. Indexing it once lets the desk search credit lines instantly, with no API calls.

    python3 -m app.met_local                    # download (~340 MB) and index
    python3 -m app.met_local --csv path/to/MetObjects.csv   # index a file you already have

The CSV and the index stay on your computer (data/ and signals.db are not committed).
"""
import argparse
import csv
import re
import sqlite3
import sys
import time

import requests

from . import config, db

CSV_URL = "https://media.githubusercontent.com/media/metmuseum/openaccess/master/MetObjects.csv"
DATA_DIR = config.ROOT / "data"
CSV_PATH = DATA_DIR / "MetObjects.csv"


def _has_fts(conn):
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def download(url=CSV_URL, path=CSV_PATH):
    DATA_DIR.mkdir(exist_ok=True)
    tmp = path.with_suffix(".part")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total, done, last = int(r.headers.get("content-length", 0)), 0, 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                if time.time() - last > 1:
                    last = time.time()
                    pct = f"{done / total:.0%}" if total else ""
                    print(f"\r  downloading MetObjects.csv: {done / 1e6:,.0f} MB {pct}   ", end="", flush=True)
    tmp.replace(path)
    print(f"\r  downloaded {path} ({done / 1e6:,.0f} MB)            ")
    return path


def build_index(path=CSV_PATH):
    """Load objects that have a credit line into SQLite (plus a full-text index when available)."""
    db.init()
    csv.field_size_limit(sys.maxsize)
    with db.connect() as conn:
        conn.execute("DROP TABLE IF EXISTS met_fts")
        conn.execute("DROP TABLE IF EXISTS met_objects")
        conn.execute("""CREATE TABLE met_objects (object_id INTEGER PRIMARY KEY, title TEXT, department TEXT,
                        classification TEXT, credit TEXT, url TEXT)""")
        rows, n = [], 0
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                credit = (r.get("Credit Line") or "").strip()
                if not credit or not (r.get("Object ID") or "").isdigit():
                    continue
                rows.append((int(r["Object ID"]), r.get("Title"), r.get("Department"), r.get("Classification"),
                             credit, r.get("Link Resource")))
                if len(rows) >= 20000:
                    conn.executemany("INSERT OR REPLACE INTO met_objects VALUES (?,?,?,?,?,?)", rows)
                    n += len(rows); rows = []
                    print(f"\r  indexed {n:,} objects", end="", flush=True)
        conn.executemany("INSERT OR REPLACE INTO met_objects VALUES (?,?,?,?,?,?)", rows)
        n += len(rows)
        if _has_fts(conn):
            conn.execute("CREATE VIRTUAL TABLE met_fts USING fts5(credit, content='met_objects', content_rowid='object_id')")
            conn.execute("INSERT INTO met_fts(rowid, credit) SELECT object_id, credit FROM met_objects")
    print(f"\r  indexed {n:,} objects with credit lines into {config.DB_PATH}")
    return n


def available():
    try:
        with db.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM met_objects").fetchone()[0] > 0
    except sqlite3.OperationalError:
        return False


def search(name, limit=12):
    """Objects whose credit line contains every part of the name (e.g. 'Gift of Jane and John Roe')."""
    parts = [p for p in re.findall(r"[\w'’-]+", name) if len(p) > 1]
    if not parts:
        return []
    with db.connect() as conn:
        try:
            q = " AND ".join('"' + p.replace('"', "") + '"' for p in parts)
            rows = conn.execute("""SELECT o.* FROM met_fts JOIN met_objects o ON o.object_id = met_fts.rowid
                                   WHERE met_fts MATCH ? LIMIT ?""", (q, limit * 4)).fetchall()
        except sqlite3.OperationalError:          # no FTS5 in this SQLite: plain LIKE scan
            where = " AND ".join("credit LIKE ?" for _ in parts)
            rows = conn.execute(f"SELECT * FROM met_objects WHERE {where} LIMIT ?",
                                [f"%{p}%" for p in parts] + [limit * 4]).fetchall()
    return [dict(r) for r in rows]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Download and index The Met's Open Access CSV")
    ap.add_argument("--csv", help="use an existing MetObjects.csv instead of downloading")
    args = ap.parse_args(argv)
    path = args.csv or CSV_PATH
    if not args.csv and not CSV_PATH.exists():
        print("Downloading The Met Open Access data (about 340 MB, one time)…")
        download()
    build_index(path)
    print("Done. The desk now searches Met credit lines locally.")


if __name__ == "__main__":
    main()
