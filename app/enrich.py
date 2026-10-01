"""Person profiles from Wikipedia + Wikidata (free, no key).

For each name: find the Wikipedia page, confirm via Wikidata that it's a human (P31 = Q5),
then pull a short bio and immediate family: spouse (P26), father (P22), mother (P25),
children (P40), plus birth/death dates, occupations and net worth where recorded.
Results — including misses — are cached in SQLite so each name is looked up once.
"""
import logging
import re

import requests

from . import db

log = logging.getLogger(__name__)

UA = {"User-Agent": "WealthSignalMonitor/0.1 (local research tool)"}
WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

FAMILY_PROPS = {"P26": "spouses", "P22": "father", "P25": "mother", "P40": "children", "P3373": "siblings"}


def name_key(name):
    return re.sub(r"\s+", " ", name.strip().lower())


def profile(name, role=None):
    key = name_key(name)
    cached = db.get_person(key)
    if cached is not None:
        return {**cached, "role": role}
    try:
        data = _lookup(name)
    except requests.RequestException as e:
        log.warning("lookup failed for %s: %s", name, e)
        return {"name": name, "role": role, "found": False, "error": "Lookup service unreachable"}
    db.put_person(key, data)
    return {**data, "role": role}


def _get(url, **params):
    r = requests.get(url, params=params or None, headers=UA, timeout=15)
    r.raise_for_status()
    return r.json()


def _lookup(name):
    hits = _get(WIKI_API, action="query", list="search", srsearch=name, srlimit=3, format="json")
    for hit in hits.get("query", {}).get("search", [])[:3]:
        title = hit["title"]
        if not _name_matches(name, title):
            continue
        summary = _get(WIKI_SUMMARY + requests.utils.quote(title.replace(" ", "_"), safe=""))
        qid = summary.get("wikibase_item")
        if not qid or summary.get("type") == "disambiguation":
            continue
        entity = _get(WIKIDATA_API, action="wbgetentities", ids=qid, props="claims|descriptions",
                      languages="en", format="json")["entities"][qid]
        claims = entity.get("claims", {})
        if "Q5" not in _item_ids(claims, "P31"):
            continue
        return _build(name, title, summary, claims)
    return {"name": name, "found": False}


def _name_matches(name, title):
    want = {t for t in re.findall(r"\w+", name.lower()) if len(t) > 1}
    have = set(re.findall(r"\w+", title.lower()))
    return len(want & have) >= max(1, len(want) - 1)


def _item_ids(claims, prop):
    out = []
    for c in claims.get(prop, []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(v, dict) and v.get("id"):
            out.append(v["id"])
    return out


def _time(claims, prop):
    for c in claims.get(prop, []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(v, dict) and v.get("time"):
            m = re.match(r"[+-](\d{4})-(\d{2})-(\d{2})", v["time"])
            if m:
                y, mo, d = m.groups()
                return y if mo == "00" else f"{y}-{mo}" if d == "00" else f"{y}-{mo}-{d}"
    return None


def _net_worth(claims):
    best = None
    for c in claims.get("P2218", []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value", {})
        amt = v.get("amount")
        when = None
        for q in c.get("qualifiers", {}).get("P585", []):
            when = q.get("datavalue", {}).get("value", {}).get("time", "")[1:5]
        if amt and (best is None or (when or "") > (best[1] or "")):
            best = (float(amt), when, v.get("unit", ""))
    if not best:
        return None
    amt, when, unit = best
    cur = "$" if unit.endswith("Q4917") else ""
    s = f"{cur}{amt / 1e9:.1f}B" if amt >= 1e9 else f"{cur}{amt / 1e6:.0f}M"
    return f"{s} ({when})" if when else s


def _labels(qids):
    if not qids:
        return {}
    out = {}
    for i in range(0, len(qids), 50):
        chunk = qids[i:i + 50]
        ents = _get(WIKIDATA_API, action="wbgetentities", ids="|".join(chunk), props="labels|sitelinks",
                    languages="en", sitefilter="enwiki", format="json")["entities"]
        for q, e in ents.items():
            label = e.get("labels", {}).get("en", {}).get("value", q)
            wiki = e.get("sitelinks", {}).get("enwiki", {}).get("title")
            out[q] = {"name": label, "url": f"https://en.wikipedia.org/wiki/{wiki.replace(' ', '_')}" if wiki else None}
    return out


def _build(name, title, summary, claims):
    family_ids = {field: _item_ids(claims, p) for p, field in FAMILY_PROPS.items()}
    occ_ids = _item_ids(claims, "P106")[:4]
    labels = _labels(sorted({q for ids in family_ids.values() for q in ids} | set(occ_ids)))

    def people(field):
        return [labels.get(q, {"name": q, "url": None}) for q in family_ids[field]]

    return {
        "name": title,
        "query_name": name,
        "found": True,
        "description": summary.get("description"),
        "bio": summary.get("extract"),
        "image": (summary.get("thumbnail") or {}).get("source"),
        "wikipedia_url": summary.get("content_urls", {}).get("desktop", {}).get("page"),
        "born": _time(claims, "P569"),
        "died": _time(claims, "P570"),
        "occupations": [labels[q]["name"] for q in occ_ids if q in labels],
        "net_worth": _net_worth(claims),
        "family": {
            "spouses": people("spouses"),
            "parents": people("father") + people("mother"),
            "children": people("children"),
            "siblings": people("siblings"),
        },
        "source": "Wikipedia / Wikidata",
    }
