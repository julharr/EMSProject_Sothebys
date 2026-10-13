"""Turn classified news articles into Signal Desk leads.

A lead is one person (merged across every article that names them) with the wealth
signals found in their coverage. Everything here is derived from the article text;
the four factor ratings are transparent heuristics, not estimates of fact.
"""
import hashlib
import json
import re
from datetime import datetime, timezone

from . import config, db
from .enrich import name_key

EVENT_TO_TYPE = {
    "Death": "death",
    "Estate & Succession": "death",
    "Divorce": "divorce",
    "Debt & Distress": "debt",
    "Collection & Property Sale": "property",
    "Liquidity Event": "liquidity",
}
SELLER_TYPES = {"death", "divorce", "debt", "property"}
SENSITIVE_TYPES = {"death", "divorce", "debt"}

PLAYBOOK = {
    "death": ("Probate usually opens one to three months after a death, and executors commission appraisals once appointed.",
              "Route through Trusts & Estates. Approach the estate attorney or executor once they are named in probate filings. No direct contact with family."),
    "divorce": ("Assets are usually valued for a settlement six to nine months after filing.",
                "Offer confidential valuation to counsel for either party through Private Sales, staying strictly neutral."),
    "debt": ("Creditor pressure can force sales within weeks or months, so speed and certainty matter.",
             "Approach through the person's advisor or counsel with a private sale or guarantee, confidentiality first."),
    "property": ("A sale is already being discussed publicly, so consignment decisions may be close.",
                 "Reach the owner's advisor or the listing broker and propose a single-owner or private sale."),
    "liquidity": ("New liquidity is usually reallocated within three to six months; IPO lock-ups typically end about 180 days after listing.",
                  "Seek a warm introduction through a mutual contact or wealth manager and invite them to a preview, with no pitch."),
}

DEPT_RULES = [
    (r"\bjewel|\bdiamond|\bsapphire|\bemerald|\bpearls?\b|\bruby\b", "Jewelry"),
    (r"\bwatch(es)?\b|\brolex\b|\bpatek\b", "Watches"),
    (r"\bwine\b|\bwhisk(e)?y\b|\bcellar\b", "Wine & Spirits"),
    (r"\bferrari\b|\bporsche\b|\bclassic cars?\b|\bsupercars?\b", "Collectible Cars"),
    (r"\bimpressionis|\bmonet\b|\brenoir\b|\bc[ée]zanne\b|\bpicasso\b", "Impressionist & Modern Art"),
    (r"\bold masters?\b|\brembrandt\b|\brubens\b", "Old Master Paintings"),
    (r"\bcontemporary art\b|\bwarhol\b|\bbasquiat\b|\brothko\b|\bhockney\b", "Contemporary Art"),
    (r"\bchinese\b.*\b(ceramics?|porcelain|works of art)\b|\bming\b|\bqing\b", "Chinese Works of Art"),
    (r"\bmanuscripts?\b|\brare books?\b", "Books & Manuscripts"),
    (r"\bmansion\b|\bpenthouse\b|\bvilla\b|\btownhouse\b|\breal estate\b|\blists? (his|her|their) home\b", "Real estate (Sotheby's International Realty)"),
    (r"\bart\b|\bpaintings?\b|\bcollection\b|\bcollector\b|\bsculpture", "Fine Art (to be assigned)"),
]
DEFAULT_DEPT = {"death": "Trusts & Estates", "divorce": "Private Sales", "debt": "Private Sales",
                "property": "Private Sales", "liquidity": "Client Development"}

REGIONS = [
    ("New York", r"\bnew york\b|\bmanhattan\b|\bbrooklyn\b|\bhamptons\b|\bsouthampton\b"),
    ("Greenwich, CT", r"\bgreenwich\b"),
    ("Palm Beach", r"\bpalm beach\b"), ("Miami", r"\bmiami\b"),
    ("Greater Boston", r"\bboston\b"), ("Washington, DC", r"\bwashington, d\.?c\.?\b"),
    ("Los Angeles", r"\blos angeles\b|\bbeverly hills\b|\bbel air\b|\bmalibu\b|\bhollywood\b"),
    ("San Francisco Bay Area", r"\bsan francisco\b|\bsilicon valley\b|\bpalo alto\b"),
    ("Texas", r"\bhouston\b|\bdallas\b|\baustin\b"), ("Chicago", r"\bchicago\b"),
    ("London", r"\blondon\b"), ("Paris", r"\bparis\b"), ("Geneva", r"\bgeneva\b|\bswitzerland\b"),
    ("Hong Kong", r"\bhong kong\b"), ("Dubai", r"\bdubai\b|\babu dhabi\b"), ("Singapore", r"\bsingapore\b"),
    ("Monaco", r"\bmonaco\b"), ("Tokyo", r"\btokyo\b"), ("Mumbai", r"\bmumbai\b"),
]

WEALTH_TIERS = [
    (95, r"\bbillionaires?\b|\brichest\b"),
    (80, r"\bmillionaires?\b|\btycoon|\bmogul|\bmagnate|\bheir(s|ess)?\b|\bdynasty\b|\bfortune\b|\bfamily office\b"),
    (65, r"\bfounder\b|\bco-founder\b|\bchairman\b|\bchairwoman\b|\bceo\b|\bhedge fund\b|\bprivate equity\b|\bsocialite\b|\broyal\b|\bprince(ss)?\b|\bduke\b"),
]
ART_TERMS = r"\bart\b|\bartworks?\b|\bpaintings?\b|\bcollector\b|\bcollection\b|\bauction|\bmuseum\b|\bgallery\b|\bjewel|\bwatches\b|\bsculpture|\bphilanthrop|\bfoundation\b|\bpatron\b"
ROLE_TERMS = r"\b(billionaire|heiress|heir|tycoon|mogul|magnate|socialite|philanthropist|art collector|collector|founder|co-founder|chairman|chairwoman|ceo|investor|developer|designer|executive)\b"


def _lead_id(key):
    return "N-" + hashlib.sha1(key.encode()).hexdigest()[:6].upper()


def _safe_url(url):
    return url if isinstance(url, str) and re.match(r"^https?://", url) else None


def _first(pattern_list, text, default=None):
    for value, rx in pattern_list:
        if re.search(rx, text, re.I):
            return value
    return default


def _days_old(iso):
    try:
        then = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
    except ValueError:
        return 999
    return (datetime.now(timezone.utc) - then).total_seconds() / 86400


def _article_rows():
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT a.*, p.mode AS pull_mode FROM articles a LEFT JOIN pulls p ON p.id = a.pull_id
               ORDER BY a.published_at DESC""").fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d["tags"] or "[]")
        d["people"] = json.loads(d["people"] or "[]")
        out.append(d)
    return out


def _signal(a, sig_type):
    return {
        "d": (a.get("published_at") or a.get("fetched_at") or "")[:10],
        "ts": a.get("published_at") or a.get("fetched_at") or "",
        "type": sig_type,
        "title": a["title"],
        "src": a.get("source") or "News report",
        "url": _safe_url(a.get("url")),
        "sid": "newsapi-top" if a.get("pull_mode") == "top" else "newsapi-search",
        "summary": a.get("description"),
        "confidence": a.get("event_confidence"),
        "rationale": a.get("event_rationale"),
        "article_id": a["id"],
    }


def build():
    articles = _article_rows()
    leads = {}

    # Pass 1: every medium/high wealth event with a named person creates or extends a lead.
    for a in articles:
        sig_type = EVENT_TO_TYPE.get(a.get("event_type"))
        if not sig_type or a.get("event_confidence") not in ("high", "medium") or not a["people"]:
            continue
        principal = a["people"][0]
        key = name_key(principal["name"])
        lead = leads.setdefault(key, {"key": key, "name": principal["name"], "roles": [], "articles": [],
                                      "signals": [], "people": {}})
        if principal.get("role"):
            lead["roles"].append(principal["role"])
        for p in a["people"][1:]:
            lead["people"].setdefault(name_key(p["name"]), {"name": p["name"], "role": p.get("role")})
        lead["articles"].append(a)
        lead["signals"].append(_signal(a, sig_type))

    # Pass 2: art/philanthropy coverage that names someone already on the desk adds affinity evidence.
    # Match the lead's full name in the text directly, so it doesn't depend on name extraction.
    for a in articles:
        if a.get("event_type") or not ({"Art & Collectibles", "Philanthropy"} & set(a["tags"])):
            continue
        text = f"{a['title']} {a.get('description') or ''}"
        for lead in leads.values():
            if re.search(r"\b" + re.escape(lead["name"]) + r"\b", text) and \
                    all(s["article_id"] != a["id"] for s in lead["signals"]):
                lead["articles"].append(a)
                lead["signals"].append(_signal(a, "philanthropy"))

    out = [_finish(l) for l in leads.values()]
    out.sort(key=lambda l: max(s["ts"] for s in l["signals"]), reverse=True)
    return out


def _finish(l):
    text = " ".join(f"{a['title']} {a.get('description') or ''}" for a in l["articles"])
    triggers = [s for s in l["signals"] if s["type"] != "philanthropy"]
    latest = max(triggers, key=lambda s: s["d"])
    t = latest["type"]

    cached = db.get_person(l["key"]) or {}
    conf_score = {"high": 90, "medium": 70}.get(latest["confidence"], 50)
    strength = min(100, conf_score + 5 * (len(triggers) - 1))
    wealth = _first(WEALTH_TIERS, text, 40)
    if cached.get("net_worth"):
        wealth = max(wealth, 90)
    art_hits = len(set(m.lower() for m in re.findall(ART_TERMS, text, re.I)))
    affinity = min(95, 30 + 15 * art_hits)
    age = min(_days_old(a.get("published_at")) for a in l["articles"])
    timing = 90 if age <= 2 else 75 if age <= 7 else 55 if age <= 30 else 35

    role_bits = l["roles"] or sorted({m.lower() for m in re.findall(ROLE_TERMS, text, re.I)})
    role = ", ".join(dict.fromkeys(role_bits))[:120].capitalize() if role_bits else "Named in news coverage"
    timing_txt, angle = PLAYBOOK[t]
    summary = latest.get("summary") or latest["title"]

    return {
        "id": _lead_id(l["key"]),
        "name": l["name"],
        "role": role,
        "side": "seller" if t in SELLER_TYPES else "buyer",
        "region": _first(REGIONS, text, "Region unknown"),
        "dept": next((dept for rx, dept in DEPT_RULES if re.search(rx, text, re.I)), DEFAULT_DEPT[t]),
        "val": None,
        "net_worth": cached.get("net_worth"),
        "f": {"s": strength, "w": wealth, "a": affinity, "t": timing},
        "status": "new",
        "sensitive": t in SENSITIVE_TYPES,
        "signals": sorted(l["signals"], key=lambda s: s["ts"]),
        "note": summary,
        "rationale": latest.get("rationale"),
        "timing": timing_txt,
        "angle": angle,
        "people": list(l["people"].values()),
    }


SOURCES = [
    {"id": "newsapi-top", "type": "mixed", "name": "NewsAPI: US top headlines",
     "what": "General headlines every other pull; catches big deaths, deals and divorces that make the front page",
     "cost": "Free (developer plan)", "url": "https://newsapi.org/"},
    {"id": "newsapi-search", "type": "mixed", "name": "NewsAPI: wealth-event search",
     "what": "Targeted search for billionaire, heir and collector stories with death, divorce, debt, estate and sale terms (plus the daily catch-up)",
     "cost": "Free (developer plan)", "url": "https://newsapi.org/"},
    {"id": "wikidata", "type": "mixed", "name": "Wikipedia and Wikidata", "enrichment": True,
     "what": "Bios, birth and death dates, net worth where recorded, spouse, parents and children for people on a lead",
     "cost": "Free", "url": "https://www.wikidata.org/"},
    {"id": "edgar", "type": "liquidity", "planned": True, "name": "SEC EDGAR (S-1, Form 4, 8-K, 13D/G)",
     "what": "Who owns what at IPO, insider share sales, lock-up expiry dates, acquisitions", "cost": "Free",
     "url": "https://www.sec.gov/edgar/search/"},
    {"id": "deeds", "type": "property", "planned": True, "name": "NYC ACRIS, county recorders",
     "what": "Deed transfers with price, buyer and seller names", "cost": "Free", "url": "https://a836-acris.nyc.gov/CP/"},
    {"id": "probate", "type": "death", "planned": True, "name": "Surrogate's and probate courts",
     "what": "Estate opened, executor and attorney named, timing", "cost": "Free to low",
     "url": "https://websurrogates.nycourts.gov/"},
    {"id": "dockets", "type": "divorce", "planned": True, "name": "State court dockets (e.g. NYSCEF, CT Judicial)",
     "what": "Filing date, counsel, motions to appraise assets", "cost": "Free to low",
     "url": "https://iapps.courts.state.ny.us/nyscef/"},
    {"id": "pacer", "type": "debt", "planned": True, "name": "PACER bankruptcy filings",
     "what": "Chapter 11 and personal guarantees", "cost": "$0.10 per page", "url": "https://pacer.uscourts.gov/"},
    {"id": "propublica", "type": "philanthropy", "planned": True, "name": "IRS Form 990-PF via ProPublica",
     "what": "Private foundation art purchases and grants", "cost": "Free",
     "url": "https://projects.propublica.org/nonprofits/"},
]


def stats():
    month_start = datetime.now(timezone.utc).replace(day=1).strftime("%Y-%m-%d")
    with db.connect() as conn:
        scanned = conn.execute("SELECT COUNT(*) FROM articles WHERE fetched_at >= ?", (month_start,)).fetchone()[0]
        flagged = conn.execute("SELECT COUNT(*) FROM articles WHERE fetched_at >= ? AND event_type IS NOT NULL",
                               (month_start,)).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    return {"scanned_month": scanned, "flagged_month": flagged, "articles_total": total}


def desk():
    return {"leads": build(), "sources": SOURCES, "stats": stats(), "demo": config.DEMO_MODE}
