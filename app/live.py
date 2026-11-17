"""Live data for the Signal Desk layout, built from free sources only.

Stories come from the NewsAPI pulls already in SQLite. Each wealth event with a named person is
a consignment signal (estate, family or financial change, announced sale) or a buy signal
(new liquidity). Buy signals get a profile assembled from free sources:

  Wikipedia + Wikidata  -> bio, occupations, recorded net worth
  The Met, Cleveland    -> credit lines naming the person, and the departments of those works
  The desk's own stories -> what the news says about them

Tags are written by transparent keyword rules (no AI). A background worker fetches the source
data; tags are recomputed on every request from the cached data, so new stories count at once.
"""
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config, db, enrich, museums
from .enrich import name_key

log = logging.getLogger(__name__)

WINDOW_DAYS = 14
BUY_EVENTS = {"Liquidity Event"}

# --- collecting categories: keyword rules over bio and news text --------------------------
CATEGORY_RULES = [
    ("contemporary", r"contemporary art|\bwarhol|basquiat|\bkoons\b|\bhirst\b|\brothko|\bhockney", "Mentions contemporary art"),
    ("impmod", r"impressionis|\bmonet\b|\brenoir\b|c[ée]zanne|\bpicasso\b|\bmatisse\b|modern art", "Mentions Impressionist or modern art"),
    ("british", r"british (art|portrait|painting)|gainsborough|\bturner\b|\bconstable\b|\breynolds\b", "Mentions British masters"),
    ("oldmasters", r"old masters?|rembrandt|\brubens\b|\bvermeer\b|\btitian\b", "Mentions Old Masters"),
    ("asian", r"asian art|chinese (art|ceramics|porcelain)|song dynasty|ming dynasty|qing dynasty|japanese art", "Mentions Asian art"),
    ("latam", r"latin american art|\bfrida kahlo\b|\btamayo\b", "Mentions Latin American art"),
    ("african", r"african (modern|contemporary)? ?art", "Mentions African art"),
    ("islamic", r"islamic art", "Mentions Islamic art"),
    ("photos", r"photograph(y|s) collect|collects? photograph|photography collection", "Collects photography"),
    ("design", r"design collection|collectible design|decorative arts", "Mentions design or decorative arts"),
    ("americana", r"americana|folk art|american silver", "Mentions Americana"),
    ("books", r"rare books?|first editions?|manuscripts?", "Mentions rare books or manuscripts"),
    ("jewelry", r"jewel|diamonds?\b|sapphire|emerald", "Mentions jewelry"),
    ("watches", r"watch collect|collects? watches|rare watch|patek|\brolex\b|audemars", "Mentions watches"),
    ("cars", r"car collect|classic cars?|\bferrari\b|concours|vintage cars?", "Mentions collectible cars"),
    ("wine", r"wine collect|vineyard|winery|wine cellar|\bcellar\b", "Mentions wine"),
    ("handbags", r"handbag|herm[eè]s|birkin", "Mentions handbags"),
    ("prints", r"\bprints\b|editions and multiples", "Mentions prints"),
    ("sports", r"team owner|owns the .{0,30}(team|club)|sports memorabilia", "Sports ownership or memorabilia"),
    ("streetwear", r"sneaker", "Mentions sneakers"),
]
AFFILIATION_RULES = [
    (r"met gala", "Met Gala attendee"),
    (r"\btrustee\b", "Trustee"),
    (r"(board|chair)[\w\s,]{0,40}museum|museum[\w\s,]{0,20}board", "Museum board"),
    (r"giving pledge", "Giving Pledge signatory"),
    (r"philanthropist|philanthropy", "Philanthropist"),
    (r"art collector|collector of", "Art collector"),
    (r"watch collect", "Watch collector"),
    (r"wine collect|vineyard|winery", "Wine collector"),
]
BUY_EVENT_LABELS = [
    (r"\bipo\b|goes public|went public|listing|lists on", "IPO"),
    (r"acquir|buyout|merger|agrees? to (buy|acquire)|sells? (his|her|their) (company|firm|business)|sale of", "Company sale"),
    (r"\bstake\b|shares|secondary", "Share sale"),
    (r"dividend|payout|windfall|settlement", "Payout"),
    (r"buys? .{0,40}(home|house|mansion|estate|penthouse|apartment|villa)", "New property"),
]
ITEM_RULES = [
    (r"jewel|diamond|sapphire|emerald|pearl", "Jewelry"),
    (r"impressionis|monet|renoir|degas", "Impressionist works"),
    (r"old master|rembrandt", "Old Master works"),
    (r"contemporary|postwar|abstract", "Postwar and contemporary art"),
    (r"paintings?|artworks?|art collection|art holdings|works by", "Art"),
    (r"watch", "Watches"),
    (r"\bcars?\b|ferrari|porsche", "Collectible cars"),
    (r"\bwine\b|cellar", "Wine"),
    (r"silver|furniture|antiques", "Decorative arts"),
    (r"drawings", "Drawings"),
    (r"manuscripts?|rare books?", "Books and manuscripts"),
    (r"mansion|penthouse|townhouse|villa|\bhome\b|real estate", "Property"),
]
WEALTH_ROLES = r"\b(billionaire|heiress|heir|tycoon|mogul|magnate|socialite|philanthropist|collector|founder|co-founder|chairman|chairwoman|ceo|investor|developer)\b"


def _iso(s):
    return (s or "").replace("Z", "+00:00")


def _since():
    return (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat(timespec="seconds")


def _articles():
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT a.* FROM articles a WHERE a.fetched_at >= ? AND a.event_type IS NOT NULL
               AND a.event_confidence IN ('high','medium') ORDER BY a.fetched_at DESC""", (_since(),)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["people"] = json.loads(d["people"] or "[]")
        if d["people"]:
            out.append(d)
    return out


def items_in(text):
    found = []
    # "a collection of maritime paintings" -> keep the phrase, but only when it names things, not people
    m = re.search(r"collection of ([a-z0-9\s\-'’,]{4,60}?)(?=[.;]| that| which| will| is| was|$)", text)
    if m and not m.group(1).strip().startswith(("the ", "his ", "her ", "their ")):
        found.append("Collection of " + m.group(1).strip(" ,"))
    for rx, label in ITEM_RULES:
        if re.search(rx, text, re.I) and label not in found:
            found.append(label)
        if len(found) >= 3:
            break
    return "; ".join(found) if found else "Not named in coverage"


def buy_event(text):
    return next((label for rx, label in BUY_EVENT_LABELS if re.search(rx, text, re.I)), "Liquidity event")


def parse_worth(label):
    """'$2.5B (2024)' -> 2.5e9; '$450M' -> 4.5e8; anything else -> None."""
    m = re.match(r"\$([\d.]+)\s*([BM])", label or "")
    if not m:
        return None
    return float(m.group(1)) * (1e9 if m.group(2) == "B" else 1e6)


# --- stories and people ------------------------------------------------------------------

def stories_and_people():
    stories, people = [], {}
    for a in _articles():
        text = f"{a['title']}. {a.get('description') or ''}"
        principal = a["people"][0]
        pid = "p:" + name_key(principal["name"])
        signal = "buy" if a["event_type"] in BUY_EVENTS else "consign"
        stories.append({
            "id": f"a{a['id']}", "pulled": a["fetched_at"], "published": a.get("published_at") or a["fetched_at"],
            "signal": signal, "event": buy_event(text) if signal == "buy" else "",
            "person": pid, "headline": a["title"], "summary": a.get("description") or "",
            "source": a.get("source") or "News report", "url": a.get("url") if str(a.get("url", "")).startswith("http") else None,
            "item": items_in(text) if signal == "consign" else None,
        })
        p = people.setdefault(pid, {"name": principal["name"], "roles": [], "texts": [], "signals": []})
        if len(principal["name"]) > len(p["name"]):
            p["name"] = principal["name"]
        if principal.get("role"):
            p["roles"].append(principal["role"])
        p["texts"].append(text)
        p["signals"].append((a["fetched_at"], signal))

    out = {}
    for pid, p in people.items():
        text = " ".join(p["texts"])
        cues = p["roles"] or sorted({m.lower() for m in re.findall(WEALTH_ROLES, text, re.I)})
        signal = max(p["signals"])[1]
        out[pid] = {"name": p["name"], "role": (", ".join(dict.fromkeys(cues)).capitalize() or "Named in coverage")[:120],
                    "signal": signal}
        if signal == "buy" or any(s == "buy" for _, s in p["signals"]):
            prof = profile_from_cache(p["name"], p["texts"])
            if prof:
                out[pid].update(prof)
            else:
                out[pid]["pending"] = True
    return stories, out


# --- profiles ----------------------------------------------------------------------------

def _cached_sources(name):
    key = name_key(name)
    return db.get_person(key), db.get_person(f"met:{key}"), db.get_person(f"cma:{key}")


def profile_from_cache(name, story_texts):
    wiki, met, cma = _cached_sources(name)
    if wiki is None:          # not looked up yet
        return None
    return build_tags(name, wiki, met or {"credits": []}, cma or {"credits": []}, story_texts)


def fetch_sources(name):
    """Network step: Wikipedia/Wikidata and both museum APIs (each cached, misses included)."""
    enrich.profile(name)
    museums.met(name)
    museums.cleveland(name)


def build_tags(name, wiki, met, cma, story_texts):
    news = " ".join(story_texts)
    bio = " ".join(filter(None, [wiki.get("bio"), wiki.get("description"), " ".join(wiki.get("occupations") or [])])) if wiki.get("found") else ""
    credits = (met.get("credits") or []) + (cma.get("credits") or [])
    tags, find = [], {}

    # Net worth: Wikidata if recorded, else "billionaire" in the coverage, else unknown.
    worth = parse_worth(wiki.get("net_worth")) if wiki.get("found") else None
    if worth:
        worth_label, worth_src = f"{wiki['net_worth']} · Wikidata", "wikipedia"
        tags.append(["wealth", f"Net worth {wiki['net_worth']}", ["wikipedia"]])
    elif re.search(r"\bbillionaire\b", news + " " + bio, re.I):
        worth, worth_label, worth_src = 1e9, "$1B+ · described as a billionaire", "news"
        tags.append(["wealth", "Described as a billionaire", ["news"]])
    else:
        worth_label, worth_src = "Unknown", None

    # Collecting categories: museum departments first (strongest evidence), then text.
    by_cat = {}
    for c in credits:
        cat = museums.category_for(c.get("department"))
        if cat:
            by_cat.setdefault(cat, {"src": set(), "why": []})
            by_cat[cat]["src"].add("met" if c["museum"] == "The Met" else "cleveland")
            by_cat[cat]["why"].append(f"{c['museum']}: {c.get('department')}")
    for cat, rx, why in CATEGORY_RULES:
        for src, text in (("wikipedia", bio), ("news", news)):
            if text and re.search(rx, text, re.I):
                by_cat.setdefault(cat, {"src": set(), "why": []})
                by_cat[cat]["src"].add(src)
                by_cat[cat]["why"].append(why)
    for cat, v in by_cat.items():
        tags.append([cat, sorted(v["src"]), "; ".join(dict.fromkeys(v["why"]))[:140]])

    # Affiliation markers.
    for kind in ("donor", "lender"):
        museums_named = sorted({c["museum"] for c in credits if c["kind"] == kind})
        for m in museums_named:
            tags.append(["aff", f"Museum {kind} ({m})", ["met" if m == "The Met" else "cleveland"]])
    seen = {t[1] for t in tags if t[0] in ("wealth", "aff")}
    for rx, label in AFFILIATION_RULES:
        for src, text in (("wikipedia", bio), ("news", news)):
            if label not in seen and text and re.search(rx, text, re.I):
                tags.append(["aff", label, [src]]); seen.add(label)
    for t in sorted({buy_event(s) for s in story_texts if s}):
        if t != "Liquidity event" or len(story_texts) == 1:
            tags.append(["wealth", f"Recent liquidity: {t}", ["news"]])

    find["wikipedia"] = ([f"Found: {wiki.get('description') or wiki.get('name')}"] +
                         ([f"Net worth on record: {wiki['net_worth']}"] if wiki.get("net_worth") else ["No net worth on record"])
                         if wiki.get("found") else [wiki.get("error") or "No Wikipedia page found"])
    find["met"] = [f"{c.get('credit')} ({c.get('department')})" for c in met.get("credits") or []][:4] or [met.get("error") or "No credit lines naming them"]
    find["cleveland"] = [f"{c.get('credit')} ({c.get('department')})" for c in cma.get("credits") or []][:4] or [cma.get("error") or "No credit lines naming them"]
    find["news"] = [f"{len(story_texts)} stor{'y' if len(story_texts) == 1 else 'ies'} on file"] if story_texts else ["No stories pulled for this name"]
    find["rules"] = [f"{len(tags)} tags from keyword rules (no AI)"]

    return {
        "bio": wiki.get("bio") if wiki.get("found") else None,
        "wikipedia_url": wiki.get("wikipedia_url"),
        "worth": worth, "worthLabel": worth_label, "worthSource": worth_src,
        "find": find, "tags": tags, "credits": credits[:8],
    }


def profile_for_name(name):
    """Profile builder request: fetch (or reuse) free source data, then tag. May take a few seconds."""
    stories, people = stories_and_people()
    pid = "p:" + name_key(name)
    texts = [f"{s['headline']}. {s['summary']}" for s in stories if s["person"] == pid]
    if not config.DEMO_MODE:
        fetch_sources(name)
    prof = profile_from_cache(name, texts) or build_tags(name, {"found": False}, {"credits": []}, {"credits": []}, texts)
    base = people.get(pid, {"name": name, "role": "Entered by hand", "signal": "buy"})
    return pid, {**base, **prof, "pending": False}


# --- background worker: look up buy-signal people as they appear --------------------------

def _due():
    _, people = stories_and_people()
    return [p["name"] for p in people.values() if p.get("pending")]


def _worker():
    while True:
        try:
            for name in _due()[:config.PROFILES_PER_PULL]:
                fetch_sources(name)
                time.sleep(1)       # be polite to free public APIs
        except Exception:           # never let a bad lookup kill the worker
            log.exception("profile worker error")
        time.sleep(60)


def start_worker():
    if config.DEMO_MODE:
        return None
    t = threading.Thread(target=_worker, name="profile-worker", daemon=True)
    t.start()
    return t
