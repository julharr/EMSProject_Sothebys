"""Forbes real-time billionaires, via the community rtb-api (github.com/komed3/rtb-api, MIT).

Each file in the repository is one API response, served from raw.githubusercontent.com, so the desk
never downloads the whole 2 GB repository:

  * api/profile/_index        one ~650 KB file: every billionaire's name, rank, net worth, list date.
                              Fetched at most once a day and kept in SQLite; names are matched locally.
  * api/profile/{uri}/info    one person's residence, industry, children, marital status, self-made,
                              title. Fetched only when a person on the desk matches, then cached.

The figures are Forbes's, copied by a third party and updated roughly monthly; each one carries its
list date. Fine for a class prototype; production use would need a Forbes data licence.
"""
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import requests

from . import config, db
from .enrich import UA

log = logging.getLogger(__name__)

STALE_DAYS = 400          # an entry last listed longer ago than this has dropped off the list


def _get(path):
    r = requests.get(f"{config.RTB_BASE.rstrip('/')}/{path}", headers=UA, timeout=30)
    r.raise_for_status()
    return r.json()


def norm(name):
    """'Abdulla Al Futtaim & family' / 'A. Jayson Adair' -> 'abdulla al futtaim' / 'a jayson adair'."""
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\s*(&|and)\s+(family|siblings|sons|daughters)\b.*$", "", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _short(n):
    """First and last name only: 'a jayson adair' -> 'a adair', used as a fallback key."""
    parts = n.split()
    return f"{parts[0]} {parts[-1]}" if len(parts) >= 2 else n


# --- the index --------------------------------------------------------------------------

def _ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS rtb_index (uri TEXT PRIMARY KEY, name TEXT, norm TEXT, short TEXT,
                    rank INTEGER, networth REAL, listed TEXT)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rtb_norm ON rtb_index(norm)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rtb_short ON rtb_index(short)")


def refresh_index(force=False):
    """Download profile/_index when the local copy is over a day old. Returns the number of entries."""
    meta = db.get_person("rtb:_meta") or {}
    fetched = meta.get("fetched_at")
    if not force and fetched and datetime.now(timezone.utc) - datetime.fromisoformat(fetched) < timedelta(hours=24):
        return meta.get("count", 0)
    data = _get("profile/_index")
    rows = [(uri, v.get("name"), norm(v.get("name")), _short(norm(v.get("name"))), v.get("rank"),
             v.get("networth"), v.get("update")) for uri, v in data.items() if v.get("name")]
    with db.connect() as conn:
        _ensure_table(conn)
        conn.execute("DELETE FROM rtb_index")
        conn.executemany("INSERT OR REPLACE INTO rtb_index VALUES (?,?,?,?,?,?,?)", rows)
    latest = max((r[6] for r in rows if r[6]), default=None)
    db.put_person("rtb:_meta", {"fetched_at": db.now_iso(), "count": len(rows), "latest": latest})
    log.info("rtb-api: %d Forbes billionaires indexed (list date %s)", len(rows), latest)
    return len(rows)


def match(name):
    """The index entry for this name: exact match first, else a unique first+last name match."""
    n = norm(name)
    if not n or " " not in n:
        return None
    try:
        with db.connect() as conn:
            _ensure_table(conn)
            rows = conn.execute("SELECT * FROM rtb_index WHERE norm=?", (n,)).fetchall()
            if not rows:
                rows = conn.execute("SELECT * FROM rtb_index WHERE short=?", (_short(n),)).fetchall()
    except Exception:
        return None
    return dict(rows[0]) if len(rows) == 1 else None


# --- one person -------------------------------------------------------------------------

def lookup(name):
    """Network step (cached): match the name, then fetch that billionaire's info file once."""
    try:
        refresh_index()
    except requests.RequestException as e:
        log.warning("rtb-api index unavailable: %s", e)
    m = match(name)
    if not m:
        return None
    key = f"rtb:{m['uri']}"
    info = db.get_person(key)
    if info is None:
        try:
            info = _get(f"profile/{m['uri']}/info")
        except requests.RequestException as e:
            log.warning("rtb-api info for %s failed: %s", m["uri"], e)
            return {**m, "info": {}}
        db.put_person(key, info)
    return {**m, "info": info}


def cached(name):
    """Same as lookup, without any network calls (for building pages)."""
    m = match(name)
    if not m:
        return None
    return {**m, "info": db.get_person(f"rtb:{m['uri']}") or {}}


def is_current(entry):
    meta = db.get_person("rtb:_meta") or {}
    latest, listed = meta.get("latest") or entry.get("listed"), entry.get("listed")
    if not listed or not latest:
        return False
    return (datetime.fromisoformat(latest) - datetime.fromisoformat(listed)).days <= STALE_DAYS


def list_date(entry):
    try:
        d = datetime.fromisoformat(entry["listed"])
        return f"{d.day} {d:%b %Y}"
    except (KeyError, TypeError, ValueError):
        return "date unknown"
