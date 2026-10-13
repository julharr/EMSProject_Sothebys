"""Pull people's names out of a headline and description without an NLP model.

News copy introduces people in a few predictable ways, and a candidate is only accepted
when at least one of them is present:
  * a role or relationship word right before the name   "co-founder Adaeze Okafor", "his wife, Celine"
  * an honorific                                         "Sir Nigel Ashcombe", "Lady Fiona Ashcombe"
  * the surname used again on its own                    "Harold Whitmore ... Whitmore, who built"
  * an age or a death right after the name               "Okafor, 38," / "Jane Roe died"
Known companies and places are masked first, and words such as "Resorts", "Beach" or
"Exchange" break a run of capitalized words, so "Goldman Sachs" or "Palm Beach" never
become people. Headlines in Title Case are only mined for names the description confirms.
"""
import re

UP = r"[A-ZÀ-ÖØ-Þ]"
LOW = r"[a-zß-öø-ÿ]"
TOKEN = rf"(?:Mc|Mac|O['’])?{UP}{LOW}+(?:-{UP}?{LOW}+|['’]{UP}{LOW}+)*"
INITIAL = rf"{UP}\."
PARTICLE = r"(?:de|da|van|von|der|den|del|della|di|du|la|le|al|bin|ben|dos|das)"
RUN_RX = re.compile(rf"(?<![\w.])(?:{TOKEN}|{INITIAL})(?:\s+(?:{PARTICLE}\s+){{0,2}}(?:{TOKEN}|{INITIAL}))*")
PIECE_RX = re.compile(rf"{TOKEN}|{INITIAL}|\b{PARTICLE}\b")

KNOWN_NON_PEOPLE = [
    "New York", "Los Angeles", "San Francisco", "San Diego", "Las Vegas", "Hong Kong", "Wall Street", "White House",
    "Supreme Court", "Silicon Valley", "United States", "United Kingdom", "United Arab Emirates", "Saudi Arabia",
    "Abu Dhabi", "Palm Beach", "Upper East Side", "Beverly Hills", "Goldman Sachs", "Morgan Stanley", "J.P. Morgan",
    "JPMorgan Chase", "Berkshire Hathaway", "Douglas Elliman", "Merrill Lynch", "Wells Fargo", "Charles Schwab",
    "Coldwell Banker", "Knight Frank", "Bank of America", "Deutsche Bank", "Credit Suisse", "Lehman Brothers",
    "Warner Bros", "Louis Vuitton", "Christian Dior", "Harry Winston", "Van Cleef", "Rolls-Royce", "Aston Martin",
    "Ernst & Young", "Bain Capital", "Sotheby's", "Christie's", "Phillips", "Bonhams", "Associated Press",
    "Federal Reserve", "Wall Street Journal", "New York Times", "Washington Post", "Financial Times",
]
_MASK_RX = re.compile(r"\b(?:" + "|".join(re.escape(x) for x in sorted(KNOWN_NON_PEOPLE, key=len, reverse=True)) + r")\b")

CUES = {
    "founder", "co-founder", "cofounder", "ceo", "coo", "cfo", "chairman", "chairwoman", "chair", "president",
    "executive", "director", "partner", "investor", "billionaire", "millionaire", "tycoon", "mogul", "magnate",
    "heir", "heiress", "socialite", "philanthropist", "collector", "designer", "developer", "banker", "broker",
    "executor", "executrix", "trustee", "attorney", "lawyer", "widow", "widower", "wife", "husband", "son",
    "daughter", "father", "mother", "brother", "sister", "nephew", "niece", "grandson", "granddaughter",
    "actor", "actress", "singer", "star", "author", "artist", "dealer", "owner", "entrepreneur", "patron",
    "financier", "industrialist", "shipowner", "late", "fiancé", "fiancée", "spouse", "heirs", "children",
    "judge", "senator", "governor", "chancellor", "baron", "baroness", "count", "countess", "duke", "duchess",
    "prince", "princess", "king", "queen", "emir", "sheikh", "rapper", "musician", "model", "architect",
    "winner", "legend", "icon", "player", "coach", "founders", "photographer", "painter", "sculptor",
}
HONORIFICS = {"sir", "dame", "lady", "lord", "mr", "mrs", "ms", "dr", "prof", "mr.", "mrs.", "ms.", "dr.", "prof."}
FUNCTION_WORDS = set("""
the a an and or but in on at for to of from with by as after before over under into amid about against near
says said dies dead died sells sold buys bought lists listed files filed seeks wins loses is are was were will
has have had who what why how new top former late breaking exclusive opinion report update watch live photos video
""".split())
ORG_WORDS = set("""
inc corp corporation co company group bank capital partners holdings fund funds foundation trust university college
institute museum gallery galleries news times post journal exchange committee council court senate congress resorts
hotels hotel media energy airlines motors technologies technology labs ventures industries brands studios records
payments broadcasting line lines shipping logistics pharma pharmaceuticals systems networks software bancorp
securities advisors management associates llc ltd plc ag sa reserve department agency office club team estate estates
auction auctions sale sales collection market markets fed party administration ministry
""".split())
PLACE_WORDS = set("""
beach coast valley island islands county city park avenue street road boulevard square hills heights lake river bay
harbor harbour springs village center centre side east west north south upper lower central district state
palazzo villa chateau château castle manor hall house tower towers building mansion ranch farm vineyard winery
""".split())
CALENDAR = set("""
monday tuesday wednesday thursday friday saturday sunday january february march april may june july august
september october november december
""".split())
STOP = CUES | HONORIFICS | FUNCTION_WORDS | ORG_WORDS | PLACE_WORDS | CALENDAR

_AFTER_ACCEPT = re.compile(r"^\s*(?:,\s*\d{1,3}\s*,|\(\d{1,3}\)|(?:,[^,]{0,80},\s*)?(?:died|dies|has died|passed away|is survived))")


def _title_case(s):
    words = [w for w in re.findall(r"[^\W\d_][\w'’]+", s) if len(w) > 3]
    return bool(words) and sum(w[0].isupper() for w in words) / len(words) > 0.7


def _pieces(run_text, offset):
    """Split a capitalized run into name chunks at stop words. Yields lists of (text, start, end)."""
    chunk = []
    for m in PIECE_RX.finditer(run_text):
        word = m.group(0)
        bare = re.sub(r"['’]s$", "", word).lower()
        if bare in STOP and not re.fullmatch(PARTICLE, word):
            if chunk:
                yield chunk
            chunk = []
            continue
        chunk.append((word, offset + m.start(), offset + m.end()))
    if chunk:
        yield chunk


def _cue_before(text, start):
    words = re.findall(r"[^\W\d_][\w'’.-]*", text[max(0, start - 60):start].lower())[-4:]
    for w in reversed(words):
        w = w.rstrip(".")
        if w in CUES or w in HONORIFICS or w.rstrip("s") in CUES:
            return w
    return None


def _bare(word):
    return re.sub(r"['’]s$", "", word)


def _mentioned(word, text):
    return re.search(r"\b" + re.escape(word) + r"\b", text) is not None


RELATIONS = {"heir", "heirs", "heiress", "widow", "widower", "wife", "husband", "son", "daughter", "father", "mother",
             "brother", "sister", "nephew", "niece", "grandson", "granddaughter", "children", "spouse", "executor"}


def _role(text, start, cue):
    """Label how the story refers to this person. 'heirs of X' describes the heirs, not X."""
    before = text[max(0, start - 40):start].lower()
    if re.search(r"\blate\s+(?:[\w-]+\s+){0,2}$", before):
        return "deceased"
    if not cue or cue in HONORIFICS:
        return None
    if cue in RELATIONS and re.search(r"\bof\s+(?:the\s+)?(?:[\w-]+\s+)?$", before):
        return None
    return {"heirs": "heir", "founders": "founder", "children": "child"}.get(cue, cue)


def _clean(chunk):
    while chunk and re.fullmatch(PARTICLE, chunk[0][0]):   # never start with "de", "la"
        chunk = chunk[1:]
    while chunk and re.fullmatch(PARTICLE, chunk[-1][0]):
        chunk = chunk[:-1]
    return chunk


def extract(title, description, limit=4):
    segments = [(_MASK_RX.sub(lambda m: "firm", t or ""), bool(t) and i == 0 and _title_case(t))
                for i, t in enumerate((title, description))]
    full = " \n ".join(s for s, _ in segments)
    found = []  # (name, role)

    for seg_idx, (seg, is_title_case) in enumerate(segments):
        other = " ".join(s for i, (s, _) in enumerate(segments) if i != seg_idx)
        for run in RUN_RX.finditer(seg):
            for chunk in _pieces(run.group(0), run.start()):
                chunk = _clean(chunk)
                words = [w for w, _, _ in chunk]
                real = [w for w in words if not re.fullmatch(PARTICLE, w) and not re.fullmatch(INITIAL, w)]
                if len(real) < 1:
                    continue
                start, end = chunk[0][1], chunk[-1][2]
                surname = re.sub(r"['’]s$", "", real[-1])
                cue = _cue_before(seg, start)
                repeated = len(re.findall(rf"\b{re.escape(surname)}\b", full)) >= 2
                after_ok = bool(_AFTER_ACCEPT.match(seg[end:]))

                if is_title_case:
                    # Headline in Title Case: trust only names the description confirms, or a cued 2-word name.
                    anchors = [i for i, w in enumerate(words)
                               if not re.fullmatch(PARTICLE, w) and _mentioned(_bare(w), other)]
                    if anchors and anchors[-1] >= 1:
                        last = anchors[-1]
                        words = words[max(0, last - 2):last + 1]
                    elif cue and len(real) >= 2:
                        words = words[:2]
                    else:
                        continue
                else:
                    if len(real) < 2 or not (cue or repeated or after_ok):
                        continue
                    words = words[:5]

                name = re.sub(r"['’]s$", "", " ".join(words))
                if len([w for w in name.split() if not re.fullmatch(PARTICLE, w)]) < 2:
                    continue
                role = _role(seg, start, cue)
                found.append((name, role))

    # Merge: keep the longest form of each person; drop bare surnames and partial repeats.
    people = []
    for name, role in found:
        low = name.lower()
        dup = next((p for p in people if low in p["name"].lower() or p["name"].lower() in low), None)
        if dup:
            if len(name) > len(dup["name"]) and not re.search(r"\b(?:" + PARTICLE + r")\b", name.split()[0], re.I):
                dup["name"] = name
            dup["role"] = dup["role"] or role
            continue
        people.append({"name": name, "role": role})
    return people[:limit]
