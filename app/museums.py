"""Free museum collection APIs: who gave or lent works, and which departments those works sit in.

  * The Met Collection API (no key): search by name, then read each object's credit line.
  * Cleveland Museum of Art Open Access (no key): filter artworks by credit line.

A credit line that names the person ("Gift of Jane Roe", "Lent by Jane Roe") is evidence of
museum giving or lending, and the object's department says what they collect. Results are cached
per name in SQLite, misses included, so each person is looked up once.
"""
import logging
import re

import requests

from . import db
from .enrich import UA, name_key

log = logging.getLogger(__name__)

MET_SEARCH = "https://collectionapi.metmuseum.org/public/collection/v1/search"
MET_OBJECT = "https://collectionapi.metmuseum.org/public/collection/v1/objects/"
CLEVELAND = "https://openaccess-api.clevelandart.org/api/artworks/"
MAX_OBJECTS = 12

# Museum department -> collecting category key (see the CATS list on the page).
DEPARTMENT_CATEGORY = [
    (r"modern and contemporary|contemporary art", "contemporary"),
    (r"modern european painting", "impmod"),
    (r"european paintings?|european painting and sculpture|robert lehman|drawings$", "oldmasters"),
    (r"asian art|chinese|japanese|korean|indian and southeast asian", "asian"),
    (r"islamic", "islamic"),
    (r"africa", "african"),
    (r"art of the americas|latin american", "latam"),
    (r"photograph", "photos"),
    (r"prints", "prints"),
    (r"decorative art|design|european sculpture and decorative", "design"),
    (r"american wing|american painting", "americana"),
]


def category_for(department):
    d = (department or "").lower()
    return next((cat for rx, cat in DEPARTMENT_CATEGORY if re.search(rx, d)), None)


def _get(url, **params):
    r = requests.get(url, params=params or None, headers=UA, timeout=15)
    r.raise_for_status()
    return r.json()


def _names_person(credit, name):
    """True when a credit line names this person (full name, or 'Mr. and Mrs. <surname>' with the first name)."""
    c = (credit or "").lower()
    parts = [p for p in re.findall(r"[\w'’-]+", name.lower()) if len(p) > 1]
    return bool(parts) and all(p in c for p in parts)


def _kind(credit):
    c = (credit or "").lower()
    if "lent by" in c or "promised gift" in c or "loan" in c:
        return "lender"
    if "gift" in c or "bequest" in c or "purchase, " in c or "funds from" in c:
        return "donor"
    return "credited"


def _cached(prefix, name, fetch):
    key = f"{prefix}:{name_key(name)}"
    hit = db.get_person(key)
    if hit is not None:
        return hit
    try:
        data = fetch(name)
    except requests.RequestException as e:
        log.warning("%s lookup failed for %s: %s", prefix, name, e)
        return {"error": "Service unreachable", "credits": []}
    db.put_person(key, data)
    return data


def met(name):
    def fetch(n):
        ids = (_get(MET_SEARCH, q=f'"{n}"') or {}).get("objectIDs") or []
        credits = []
        for oid in ids[:MAX_OBJECTS]:
            o = _get(MET_OBJECT + str(oid))
            if _names_person(o.get("creditLine"), n):
                credits.append({"museum": "The Met", "title": o.get("title"), "department": o.get("department"),
                                "credit": o.get("creditLine"), "kind": _kind(o.get("creditLine")),
                                "url": o.get("objectURL")})
        return {"credits": credits}
    return _cached("met", name, fetch)


def cleveland(name):
    def fetch(n):
        rows = (_get(CLEVELAND, credit=n, limit=MAX_OBJECTS) or {}).get("data") or []
        credits = [{"museum": "Cleveland Museum of Art", "title": r.get("title"), "department": r.get("department"),
                    "credit": r.get("creditline"), "kind": _kind(r.get("creditline")), "url": r.get("url")}
                   for r in rows if _names_person(r.get("creditline"), n)]
        return {"credits": credits}
    return _cached("cma", name, fetch)
