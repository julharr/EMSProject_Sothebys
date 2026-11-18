"""Live data for the Signal Desk layout, built from free sources only.

Stories come from the NewsAPI pulls already in SQLite. Each wealth event with a named person is
a consignment signal (estate, family or financial change, announced sale) or a buy signal
(new liquidity). Buy signals get a profile assembled from free sources:

  Wikipedia + Wikidata  -> bio, occupations, recorded net worth
  The Met, Cleveland,   -> credit lines naming the person, and the departments of those works
  Harvard (free key)
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

from . import config, db, edgar, enrich, museums, rtb
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
    (r"\bipo\b|goes public|go public|went public|public debut|market debut|listing|lists on|\bspac\b", "IPO"),
    (r"funding round|raises? \$|series [a-f]\b|valued at", "Funding round"),
    (r"(net worth|fortune|wealth) (soars|surges|jumps|climbs|rises|doubles|tops|hits)|becomes? (a )?billionaire|new billionaire|richest", "Fortune jump"),
    (r"acquir|buyout|merger|agrees? to (buy|acquire)|sells? (a |the |his |her |their )?(company|firm|business|startup)|sale of", "Company sale"),
    (r"\bstake\b|shares|secondary|cash(es|ed)? out|tender offer", "Share sale"),
    (r"dividend|payout|windfall|settlement", "Payout"),
    (r"(buys?|bought|snaps up|purchas\w*) .{0,40}(home|house|mansion|estate|penthouse|apartment|villa|ranch|island)", "New property"),
    (r"(buys?|bought|snaps up|purchas\w*) .{0,40}(yacht|jet|team|franchise)", "New purchase"),
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


_ABBREV = r"(?:Mr|Mrs|Ms|Dr|Jr|Sr|St|Inc|Co|Corp|Ltd|No|vs|etc|U\.S|U\.K|e\.g|i\.e|[A-Z])\."


def short_bio(text, sentences=2):
    """First sentences of a Wikipedia intro, without splitting on 'Jr.', 'Inc.', 'U.S.' or initials."""
    if not text:
        return None
    t = re.sub(r"\s*\([^()]*\)", "", text).strip()                 # drop parentheticals (birth dates, pronunciations)
    protected = re.sub(_ABBREV, lambda m: m.group(0).replace(".", "\u2024"), t)
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\u201c\"])", protected)
    out = " ".join(parts[:sentences]).replace("\u2024", ".")
    return re.sub(r"\s{2,}", " ", out)


def bio_facts(wiki):
    """Key Wikidata facts (CC0) as short label/value pairs."""
    if not wiki.get("found"):
        return []
    f = []
    if wiki.get("born"):
        f.append(["Born", wiki["born"][:4]])
    if wiki.get("died"):
        f.append(["Died", wiki["died"][:4]])
    if wiki.get("occupations"):
        f.append(["Occupation", ", ".join(wiki["occupations"][:3])])
    if wiki.get("net_worth"):
        f.append(["Net worth", wiki["net_worth"]])
    fam = wiki.get("family") or {}
    if fam.get("spouses"):
        f.append(["Spouse", ", ".join(p["name"] for p in fam["spouses"][:2])])
    if fam.get("children"):
        f.append(["Children", str(len(fam["children"]))])
    return f


def forbes_facts(info, wiki):
    """Residence, children and marital status from the Forbes profile, where Wikidata has none."""
    f, have = [], {k for k, _ in bio_facts(wiki)}
    res = info.get("residence") or {}
    where = ", ".join(x for x in (res.get("city"), res.get("state"), (res.get("country") or "").upper()) if x)
    if where:
        f.append(["Lives in", where])
    if info.get("maritalStatus"):
        f.append(["Marital status", info["maritalStatus"]])
    if info.get("children") and "Children" not in have:
        f.append(["Children", str(info["children"])])
    if info.get("birthDate") and "Born" not in have:
        f.append(["Born", info["birthDate"][:4]])
    return f


def parse_worth(label):
    """'$2.5B (2024)' -> 2.5e9; '$450M' -> 4.5e8; anything else -> None."""
    m = re.match(r"\$([\d.]+)\s*([BM])", label or "")
    if not m:
        return None
    return float(m.group(1)) * (1e9 if m.group(2) == "B" else 1e6)


# --- stories and people ------------------------------------------------------------------

def _tokens(name):
    return re.findall(r"[a-z0-9]+", rtb.norm(name))


def canonical_names(names):
    """Map each name to the form the desk should show, merging variants of the same person:
    'Inside Jensen Huang' and 'Jensen H Huang' -> 'Jensen Huang' (the most common, else shortest, form)."""
    counts = {}
    for n in names:
        counts[n] = counts.get(n, 0) + 1
    forms = sorted(counts, key=lambda n: (-counts[n], len(n)))
    canon = {}
    for n in sorted(counts, key=len):
        t = _tokens(n)
        target = n
        for m in forms:
            u = _tokens(m)
            if m == n or len(u) < 2 or len(u) > len(t):
                continue
            suffix = t[-len(u):] == u                                   # "inside jensen huang" ends with "jensen huang"
            first_last = len(u) == 2 and t[0] == u[0] and t[-1] == u[-1]   # "jensen h huang" ~ "jensen huang"
            if suffix or first_last or t == u:
                target = canon.get(m, m)
                break
        canon[n] = target
    return canon


def _is_person(person):
    """False when a Wikipedia lookup showed the 'name' is a company, product or place ('Cash App')."""
    hit = db.get_person(name_key(person["name"]))
    return not (hit and hit.get("not_person"))


def stories_and_people():
    stories, people = [], {}
    arts = []
    for a in _articles():
        named = [p for p in a["people"] if _is_person(p)]
        if named:
            arts.append((a, named))
    canon = canonical_names([p["name"] for _, named in arts for p in named])
    for a, named in arts:
        text = f"{a['title']}. {a.get('description') or ''}"
        principal = {**named[0], "name": canon.get(named[0]["name"], named[0]["name"])}
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
    return db.get_person(key), db.get_person(f"met:{key}"), db.get_person(f"cma:{key}"), db.get_person(f"ham:{key}")


def profile_from_cache(name, story_texts):
    wiki, met, cma, ham = _cached_sources(name)
    if wiki is None:          # not looked up yet
        return None
    return build_tags(name, wiki, met or {"credits": []}, cma or {"credits": []}, story_texts, ham)


def fetch_sources(name):
    """Network step: Wikipedia/Wikidata and both museum APIs (each cached, misses included)."""
    enrich.profile(name)
    museums.met(name)
    museums.cleveland(name)
    museums.harvard(name)
    if config.RTB_ENABLED:
        rtb.lookup(name)


MUSEUM_SRC = {"The Met": "met", "Cleveland Museum of Art": "cleveland", "Harvard Art Museums": "harvard"}


def build_tags(name, wiki, met, cma, story_texts, ham=None, sec=None, forbes=None):
    ham = ham or {"credits": [], "error": None if config.HARVARD_API_KEY else "Not set up (add HARVARD_API_KEY to .env)"}
    news = " ".join(story_texts)
    bio = " ".join(filter(None, [wiki.get("bio"), wiki.get("description"), " ".join(wiki.get("occupations") or [])])) if wiki.get("found") else ""
    credits = (met.get("credits") or []) + (cma.get("credits") or []) + (ham.get("credits") or [])
    tags, find = [], {}

    # Net worth: Forbes (rtb-api) when they are on the current list, else Wikidata, else "billionaire"
    # in the coverage, else SEC holdings (below), else unknown.
    if forbes is None and config.RTB_ENABLED:
        forbes = rtb.cached(name)
    fb_current = bool(forbes) and rtb.is_current(forbes) and not (forbes.get("info") or {}).get("deceased")
    worth = parse_worth(wiki.get("net_worth")) if wiki.get("found") else None
    if fb_current and forbes.get("networth"):
        worth = forbes["networth"] * 1e6
        worth_label, worth_src = f"{edgar.money(worth)} · Forbes, {rtb.list_date(forbes)}", "forbes"
        tags.append(["wealth", f"Forbes billionaire #{forbes.get('rank')} ({rtb.list_date(forbes)[-8:]})", ["forbes"]])
    elif worth:
        worth_label, worth_src = f"{wiki['net_worth']} · Wikidata", "wikipedia"
        tags.append(["wealth", f"Net worth {wiki['net_worth']}", ["wikipedia"]])
    elif re.search(r"\bbillionaire\b", news + " " + bio, re.I):
        worth, worth_label, worth_src = 1e9, "$1B+ · described as a billionaire", "news"
        tags.append(["wealth", "Described as a billionaire", ["news"]])
    else:
        worth_label, worth_src = "Unknown", None

    # SEC Form 4: stock still held after a sale is a floor on wealth; use it when it beats the above.
    sec = edgar.insiders_for(name) if sec is None else sec
    if sec:
        top = max(sec, key=lambda r: r.get("holdings_value") or 0)
        held = top.get("holdings_value") or 0
        if held and (worth is None or (worth_src not in ("wikipedia", "forbes") and held > worth)):
            tk = top.get("ticker") or top.get("issuer")
            worth, worth_label, worth_src = held, f"{edgar.money(held)}+ in {tk} stock · SEC Form 4", "sec"
        for r in sec[:3]:
            tk = r.get("ticker") or r.get("issuer")
            tags.append(["wealth", f"Insider sale: {edgar.money(r.get('sale_value') or 0)} of {tk} (SEC Form 4, {str(r.get('filed_at'))[:10]})", ["sec"]])
        for role, issuer in dict.fromkeys((r.get("role"), r.get("issuer")) for r in sec):
            tags.append(["aff", f"{role} at {issuer}", ["sec"]])

    info = (forbes or {}).get("info") or {}
    if fb_current:
        sm = info.get("selfMade") or {}
        if sm.get("_is"):
            tags.append(["wealth", "Self-made (Forbes)", ["forbes"]])
        elif info.get("family"):
            tags.append(["wealth", "Family fortune (Forbes)", ["forbes"]])
        org = info.get("organization") or {}
        if org.get("name") and org.get("title"):
            tags.append(["aff", f"{org['title']}, {org['name']}", ["forbes"]])

    # Collecting categories: museum departments first (strongest evidence), then text.
    by_cat = {}
    for c in credits:
        cat = museums.category_for(c.get("department"))
        if cat:
            by_cat.setdefault(cat, {"src": set(), "why": []})
            by_cat[cat]["src"].add(MUSEUM_SRC.get(c["museum"], "met"))
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
            tags.append(["aff", f"Museum {kind} ({m})", [MUSEUM_SRC.get(m, "met")]])
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
    find["harvard"] = [f"{c.get('credit')} ({c.get('department')})" for c in ham.get("credits") or []][:4] or [ham.get("error") or "No credit lines naming them"]
    find["sec"] = [f"{r.get('role')}, {r.get('issuer')}: sold {edgar.money(r.get('sale_value') or 0)}, still holds {edgar.money(r.get('holdings_value') or 0)} ({str(r.get('filed_at'))[:10]})"
                   for r in sec][:4] or ["No Form 4 insider sales on file"]
    if forbes:
        res = info.get("residence") or {}
        where = ", ".join(x for x in (res.get("city"), res.get("state"), (res.get("country") or "").upper()) if x)
        find["forbes"] = ([f"On the Forbes list: #{forbes.get('rank')}, {edgar.money((forbes.get('networth') or 0) * 1e6)} ({rtb.list_date(forbes)})"]
                          if fb_current else [f"Last on the Forbes list {rtb.list_date(forbes)}" + (" (deceased)" if info.get("deceased") else "")]) + \
                         ([f"Lives in {where}"] if where else []) + \
                         ([f"Wealth from {', '.join(info['source'][:3])}"] if info.get("source") else [])
    else:
        find["forbes"] = ["Not on the Forbes billionaires list (or name did not match)"] if config.RTB_ENABLED else ["Turned off (RTB_ENABLED=false)"]
    find["news"] = [f"{len(story_texts)} stor{'y' if len(story_texts) == 1 else 'ies'} on file"] if story_texts else ["No stories pulled for this name"]
    find["rules"] = [f"{len(tags)} tags from keyword rules (no AI)"]

    return {
        "bio": wiki.get("bio") if wiki.get("found") else None,
        "short_bio": short_bio(wiki.get("bio")) if wiki.get("found") else None,
        "bio_facts": [f for f in bio_facts(wiki) if not (worth_src == "forbes" and f[0] == "Net worth")] + forbes_facts(info, wiki),
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
    prof = profile_from_cache(name, texts) or build_tags(name, {"found": False}, {"credits": []}, {"credits": []}, texts, None)
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
