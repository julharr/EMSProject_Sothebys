"""SEC EDGAR insider sales -> buy signals (free, no key).

Every Form 4 filing reports a trade by a company insider (officer, director or 10% owner).
An open-market sale (transaction code "S") worth at least EDGAR_MIN_SALE_USD is new liquidity
for a named person, so it becomes a buy signal in the feed. The shares the person still holds
after the sale, times the sale price, give a lower bound on their wealth for the profile builder.

Sources (https://www.sec.gov/search-filings/edgar-application-programming-interfaces):
  * latest Form 4 filings, Atom:  /cgi-bin/browse-edgar?action=getcurrent&type=4&output=atom
  * past days, daily form index:  /Archives/edgar/daily-index/YYYY/QTRn/form.YYYYMMDD.idx
  * one filing, full text:        /Archives/edgar/data/{cik}/{accession}.txt

SEC rules: a User-Agent naming the tool and an email contact, and at most 10 requests a second.
"""
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

import requests

from . import config, db
from .enrich import name_key

log = logging.getLogger(__name__)

SEC = "https://www.sec.gov"
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}
SOURCE = "SEC EDGAR (Form 4)"
MIN_INTERVAL = 0.15              # seconds between requests: under the SEC's 10 a second
_rate_lock = threading.Lock()
_last = [0.0]

ENTITY = re.compile(r"\b(llc|l\.?p\.?|inc|corp|corporation|co|company|fund|funds|trust|trusts|partners|partnership|"
                    r"holdings?|capital|ventures?|management|advisors?|group|foundation|ltd|limited|plc|gmbh|s\.?a\.?|"
                    r"n\.?v\.?|bank|associates|investments?|equity|family office|estate of)\b", re.I)


def headers():
    return {"User-Agent": f"SignalDesk research prototype {config.CONTACT}", "Accept-Encoding": "gzip, deflate"}


def _get(url):
    with _rate_lock:
        wait = MIN_INTERVAL - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    r = requests.get(url, headers=headers(), timeout=30)
    r.raise_for_status()
    return r.text


# --- finding Form 4 filings --------------------------------------------------------------

def latest_filings(count=100):
    """(cik, accession) pairs for the newest Form 4 filings, from the EDGAR Atom feed."""
    root = ET.fromstring(_get(f"{SEC}/cgi-bin/browse-edgar?action=getcurrent&type=4&owner=include&count={count}&output=atom"))
    out = {}
    for e in root.findall("a:entry", ATOM_NS):
        link = e.find("a:link", ATOM_NS)
        href = link.get("href") if link is not None else ""
        m = re.search(r"/data/(\d+)/(\d{10})(\d{2})(\d{6})/", href)
        if m:
            out.setdefault(f"{m.group(2)}-{m.group(3)}-{m.group(4)}", m.group(1))
    return [(cik, acc) for acc, cik in out.items()]


def daily_filings(day):
    """(cik, accession) pairs for every Form 4 filed on one past day, from the daily form index."""
    q = (day.month - 1) // 3 + 1
    try:
        text = _get(f"{SEC}/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx")
    except requests.HTTPError as e:          # weekends and holidays have no index
        if e.response is not None and e.response.status_code in (403, 404):
            return []
        raise
    out = []
    for line in text.splitlines():
        if not line.startswith("4 "):        # "4" only; skips 4/A amendments and 40-F etc.
            continue
        m = re.search(r"edgar/data/(\d+)/(\d{10}-\d{2}-\d{6})\.txt", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


# --- reading one Form 4 -----------------------------------------------------------------

def _t(el, path):
    found = el.find(path) if el is not None else None
    return (found.text or "").strip() if found is not None and found.text else ""


def _num(el, path):
    try:
        return float(_t(el, path).replace(",", ""))
    except ValueError:
        return 0.0


def person_name(raw):
    """EDGAR files owners as 'LAST FIRST MIDDLE' ('OKAFOR ADAEZE N'); return 'Adaeze N Okafor'."""
    parts = re.sub(r"[,]+", " ", raw).split()
    if len(parts) < 2:
        return None
    suffix = [p for p in parts[1:] if p.upper().rstrip(".") in ("JR", "SR", "II", "III", "IV")]
    rest = [p for p in parts[1:] if p not in suffix]
    def cap(w):
        w = w.lower()
        w = "-".join(x[:1].upper() + x[1:] for x in w.split("-"))
        return re.sub(r"^(Mc|Mac)([a-z])", lambda m: m.group(1) + m.group(2).upper(), w) if len(w) > 4 else w
    return " ".join(cap(w) for w in rest + [parts[0]] + suffix)


def parse_form4(text):
    """The ownershipDocument inside a filing -> issuer, human owners, and open-market sales."""
    m = re.search(r"<ownershipDocument>.*?</ownershipDocument>", text, re.S)
    if not m:
        return None
    doc = ET.fromstring(m.group(0))
    issuer = _t(doc, "issuer/issuerName")
    ticker = _t(doc, "issuer/issuerTradingSymbol").upper()
    owners = []
    for o in doc.findall("reportingOwner"):
        raw = _t(o, "reportingOwnerId/rptOwnerName")
        rel = o.find("reportingOwnerRelationship")
        role = (_t(rel, "officerTitle") if _t(rel, "isOfficer") in ("1", "true") else "") or \
               ("Director" if _t(rel, "isDirector") in ("1", "true") else "") or \
               ("10% owner" if _t(rel, "isTenPercentOwner") in ("1", "true") else "Insider")
        if raw and not ENTITY.search(raw):
            name = person_name(raw)
            if name:
                owners.append({"name": name, "role": role})
    sales, holdings = [], 0.0
    for tx in doc.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if _t(tx, "transactionCoding/transactionCode") != "S" or _t(tx, "transactionAmounts/transactionAcquiredDisposedCode/value") != "D":
            continue
        shares = _num(tx, "transactionAmounts/transactionShares/value")
        price = _num(tx, "transactionAmounts/transactionPricePerShare/value")
        sales.append({"date": _t(tx, "transactionDate/value"), "shares": shares, "price": price})
        after = _num(tx, "postTransactionAmounts/sharesOwnedFollowingTransaction/value")
        if price:
            holdings = after * price          # last sale wins: what they still hold, at that price
    return {"issuer": issuer, "ticker": ticker, "owners": owners, "sales": sales,
            "sale_value": sum(s["shares"] * s["price"] for s in sales), "holdings_value": holdings}


def money(v):
    return f"${v / 1e9:.1f}B" if v >= 1e9 else f"${v / 1e6:.1f}M"


def to_signals(f, cik, accession, filed_at):
    """One qualifying filing -> article rows (one per human owner) + insider rows."""
    if not f or not f["owners"] or f["sale_value"] < config.EDGAR_MIN_SALE_USD:
        return [], []
    url = f"{SEC}/Archives/edgar/data/{cik}/{accession.replace('-', '')}/{accession}-index.htm"
    shares = sum(s["shares"] for s in f["sales"])
    avg = f["sale_value"] / shares if shares else 0
    ticker = f" ({f['ticker']})" if f["ticker"] else ""
    articles, insiders = [], []
    for o in f["owners"]:
        held = f" and still holds about {money(f['holdings_value'])} of stock" if f["holdings_value"] else ""
        articles.append({
            "url": url if len(f["owners"]) == 1 else f"{url}#{name_key(o['name']).replace(' ', '-')}",
            "title": f"{o['name']} sells {money(f['sale_value'])} of {f['issuer']}{ticker} stock",
            "description": f"{o['name']}, {o['role']} at {f['issuer']}, sold {shares:,.0f} shares at about ${avg:,.2f}{held}, "
                           f"according to an SEC Form 4 filing.",
            "source": SOURCE, "published_at": filed_at, "tags": ["Markets & Finance", "Business & Deals"],
            "event_type": "Liquidity Event", "event_confidence": "high",
            "event_rationale": f"SEC Form 4: open-market sale of {money(f['sale_value'])} by an insider. New liquidity for a named person.",
            "people": [{"name": o["name"], "role": f"{o['role']}, {f['issuer']}"}], "classifier": "sec",
        })
        insiders.append((accession, name_key(o["name"]), o["name"], f["issuer"], f["ticker"], o["role"],
                         f["sale_value"], f["holdings_value"], filed_at, url))
    return articles, insiders


# --- running it -------------------------------------------------------------------------

def _unseen(pairs):
    with db.connect() as conn:
        seen = {r[0] for r in conn.execute(
            "SELECT accession FROM sec_seen WHERE accession IN (%s)" % ",".join("?" * len(pairs)), [a for _, a in pairs])} if pairs else set()
    return [(c, a) for c, a in pairs if a not in seen]


def process(pairs, mode, filed_at=None):
    """Fetch and read each unseen filing; store qualifying sales as buy-signal stories."""
    todo = _unseen(pairs)
    pull_id = db.start_pull(mode, counts_against_quota=False)
    new = 0
    try:
        for cik, acc in todo:
            try:
                f = parse_form4(_get(f"{SEC}/Archives/edgar/data/{cik}/{acc}.txt"))
            except (requests.RequestException, ET.ParseError) as e:
                log.warning("Form 4 %s skipped: %s", acc, e)
                continue
            when = filed_at or db.now_iso()
            arts, ins = to_signals(f, cik, acc, when)
            with db.connect() as conn:
                conn.execute("INSERT OR IGNORE INTO sec_seen VALUES (?, ?)", (acc, db.now_iso()))
                conn.executemany("INSERT OR REPLACE INTO sec_insiders VALUES (?,?,?,?,?,?,?,?,?,?)", ins)
            if arts:
                new += len(db.insert_articles(arts, pull_id))
        db.finish_pull(pull_id, "ok", n_returned=len(todo), n_new=new)
    except Exception as e:
        db.finish_pull(pull_id, "error", n_returned=len(todo), n_new=new, error=str(e))
        raise
    log.info("EDGAR %s: %d new filings read, %d insider-sale signals", mode, len(todo), new)
    return {"read": len(todo), "new": new}


def poll():
    return process(latest_filings(), "sec:latest")


def backfill(days=None, today=None):
    """Past business days from the daily index, once each (finished days are never re-read)."""
    today = today or datetime.now(timezone.utc).date()
    results = []
    for back in range(days if days is not None else config.EDGAR_BACKFILL_DAYS, 0, -1):
        d = today - timedelta(days=back)
        if d.weekday() >= 5 or db.last_pull_of_mode(f"sec:day:{d}"):
            continue
        results.append(process(daily_filings(d), f"sec:day:{d}", filed_at=f"{d}T21:00:00+00:00"))
    return results


def insiders_for(name):
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM sec_insiders WHERE name_key=? ORDER BY filed_at DESC", (name_key(name),)).fetchall()
    return [dict(r) for r in rows]


def _worker():
    if "@" not in config.CONTACT:
        log.warning("EDGAR: set CONTACT=you@yourdomain in .env; the SEC asks for an email in the User-Agent and may block requests without one")
    try:
        backfill()
    except Exception:
        log.exception("EDGAR backfill failed")
    while True:
        try:
            poll()
        except Exception:
            log.exception("EDGAR poll failed")
        time.sleep(config.EDGAR_INTERVAL_MINUTES * 60)


def start_worker():
    if config.DEMO_MODE or not config.EDGAR_ENABLED:
        return None
    t = threading.Thread(target=_worker, name="edgar-worker", daemon=True)
    t.start()
    return t
