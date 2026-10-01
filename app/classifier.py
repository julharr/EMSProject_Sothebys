"""Tag articles and flag wealth-changing events.

Two engines with the same output shape:
  * RulesClassifier  — keyword/regex, free, instant, decent recall, noisy on people.
  * ClaudeClassifier — one batched Claude call per pull; far better at telling
                       "billionaire dies" from "man dies", and at naming the people.
Each article dict gains: tags, event_type, event_confidence, event_rationale, people, classifier.
"""
import json
import logging
import re

from . import config
from .taxonomy import (CONFIDENCE_LEVELS, EVENT_NAMES, TAG_NAMES, TAGS,
                       WEALTH_CONTEXT, WEALTH_EVENTS)

log = logging.getLogger(__name__)

_TAG_RX = {t: [re.compile(p, re.I) for p in ps] for t, ps in TAGS.items()}
_EVT_RX = {
    e: {k: [re.compile(p, re.I) for p in spec[k]] for k in ("strong", "weak", "negative")}
    for e, spec in WEALTH_EVENTS.items()
}
_CTX_RX = [re.compile(p, re.I) for p in WEALTH_CONTEXT]


def _text(a):
    return f"{a.get('title') or ''}. {a.get('description') or ''}"


def _hits(rxs, text):
    """Matched snippets, each widened to whole words ("divorc" -> "divorce")."""
    out = []
    for rx in rxs:
        m = rx.search(text)
        if m:
            end = re.match(r"\w*", text[m.end():]).end() + m.end()
            out.append(text[m.start():end])
    return out


# ---------------------------------------------------------------- rules engine

_NAME_TOKEN = r"[A-Z][a-z]+(?:[-'’][A-Z]?[a-z]+)*|Mc[A-Z][a-z]+|Mac[A-Z][a-z]+|[A-Z]\."
_PARTICLE = r"(?:de|da|van|von|der|del|della|di|du|la|le|al|bin|ben|dos|das)"
_NAME_RX = re.compile(rf"\b(?:{_NAME_TOKEN})(?:\s+(?:{_PARTICLE}\s+)?(?:{_NAME_TOKEN})){{1,3}}\b")
_NOT_NAME = set("""
The A An And Or But In On At For To Of From With By As After Before Over Under Why How What When Where Who
New York Los Angeles San Francisco Hong Kong United States Kingdom Wall Street White House Supreme Court
Federal Reserve Silicon Valley Palm Beach Beverly Hills Upper East Side Middle East South North West East
Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May June July August
September October November December Inc Corp Group Bank Capital Partners Holdings Fund Foundation University
Museum Gallery Company News Times Post Journal Street Court House Senate Congress Billionaire Tycoon Heir
Heiress Mogul Founder Chairman Chief Executive Officer CEO President Dies Divorce Sells Estate Auction Art
Sotheby Sotheby's Christie Christie's Report Reuters Bloomberg Associated Press Breaking Exclusive Opinion
Mr Mrs Ms Dr Sir Lady Lord Late Family Trust Co-founder Cofounder Avenue Park Square Road Boulevard
Resorts Hotels Media Energy Airlines Motors Technologies Labs Ventures Industries Brands Studios Records
""".split())


def _looks_title_case(s):
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’]+", s) if len(w) > 3]
    return bool(words) and sum(w[0].isupper() for w in words) / len(words) > 0.7


def extract_people(article, limit=4):
    sources = []
    if article.get("title") and not _looks_title_case(article["title"]):
        sources.append(article["title"])
    if article.get("description"):
        sources.append(article["description"])
    seen, people = set(), []
    for s in sources:
        for m in _NAME_RX.finditer(s):
            name = re.sub(r"['’]s$", "", m.group(0)).strip()
            toks = name.split()
            # trim leading non-name words ("Billionaire Jane Doe" -> "Jane Doe")
            while toks and toks[0] in _NOT_NAME:
                toks = toks[1:]
            if len(toks) < 2 or any(t in _NOT_NAME for t in toks):
                continue
            name = " ".join(toks)
            if name.lower() not in seen:
                seen.add(name.lower())
                people.append({"name": name, "role": None})
    return people[:limit]


def classify_rules(article):
    text = _text(article)

    scored = sorted(((len(_hits(rxs, text)), t) for t, rxs in _TAG_RX.items()), reverse=True)
    tags = [t for n, t in scored if n > 0][:3] or ["Other"]

    ctx = _hits(_CTX_RX, text)
    best = None
    for evt, rx in _EVT_RX.items():
        if _hits(rx["negative"], text):
            continue
        strong, weak = _hits(rx["strong"], text), _hits(rx["weak"], text)
        if not strong and not weak:
            continue
        score = 3 * len(strong) + len(weak) + (2 if ctx else 0)
        if best is None or score > best[0]:
            best = (score, evt, strong, weak)

    event_type = confidence = rationale = None
    if best:
        _, event_type, strong, weak = best
        if strong and ctx:
            confidence = "high"
        elif strong or (weak and ctx):
            confidence = "medium"
        else:
            confidence = "low"
        cues = ", ".join(f"“{h.lower()}”" for h in (strong + weak)[:3])
        rationale = f"Keyword match: {cues}."
        if ctx:
            rationale += f" Wealth context: {', '.join(sorted({c.lower() for c in ctx})[:3])}."
        rationale += " " + WEALTH_EVENTS[event_type]["blurb"]

    return {
        **article,
        "tags": tags,
        "event_type": event_type,
        "event_confidence": confidence,
        "event_rationale": rationale,
        "people": extract_people(article),
        "classifier": "rules",
    }


# ---------------------------------------------------------------- Claude engine

SYSTEM_PROMPT = f"""You are a research analyst on the client-development team of a major auction house \
(think Sotheby's). You screen news headlines for wealth-changing events affecting high-net-worth \
individuals and families — events that often lead to art, jewelry, collection or property sales.

For each article, return:
- tags: 1-3 topic tags, chosen only from: {", ".join(TAG_NAMES)}.
- event_type: one of {", ".join(EVENT_NAMES)}, or "None".
  Only flag an event when it plausibly changes the wealth or asset position of an identifiable wealthy \
person, family, or estate. "Billionaire collector dies" is Death; "three die in storm" is None. National or \
corporate debt with no wealthy individual is None. An ordinary corporate acquisition is a Liquidity Event \
only if a founder/owner/family is likely cashing out.
- confidence: "high" when the event and a wealthy principal are both explicit; "medium" when one is inferred; \
"low" when speculative. Use "low" with event_type "None".
- rationale: one or two sentences on why this matters to an auction house client team (or why not). \
Do not invent facts beyond the headline and description.
- people: the key named individuals in the story (max 4), each with name (full name as written) and role \
(a short phrase such as "deceased", "spouse filing for divorce", "heir", "founder selling stake"). \
Only real named people that appear in the text; empty list if none."""

_SCHEMA = {
    "type": "object",
    "properties": {
        "articles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "tags": {"type": "array", "items": {"type": "string", "enum": TAG_NAMES}},
                    "event_type": {"type": "string", "enum": EVENT_NAMES + ["None"]},
                    "confidence": {"type": "string", "enum": CONFIDENCE_LEVELS},
                    "rationale": {"type": "string"},
                    "people": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"name": {"type": "string"}, "role": {"type": "string"}},
                            "required": ["name", "role"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["id", "tags", "event_type", "confidence", "rationale", "people"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["articles"],
    "additionalProperties": False,
}


class ClaudeClassifier:
    BATCH = 50

    def __init__(self):
        import anthropic
        self.anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    def classify(self, articles):
        out = []
        for i in range(0, len(articles), self.BATCH):
            out.extend(self._batch(articles[i:i + self.BATCH]))
        return out

    def _batch(self, articles):
        payload = [
            {"id": i, "title": a["title"], "description": a.get("description") or "", "source": a.get("source") or ""}
            for i, a in enumerate(articles)
        ]
        try:
            with self.client.beta.messages.stream(
                model=config.CLAUDE_MODEL,
                max_tokens=32000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                thinking={"type": "adaptive"},
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": _SCHEMA}},
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": "Classify these articles:\n" + json.dumps(payload, ensure_ascii=False)}],
            ) as stream:
                msg = stream.get_final_message()
            if msg.stop_reason in ("refusal", "max_tokens"):
                raise RuntimeError(f"stop_reason={msg.stop_reason}")
            text = next(b.text for b in msg.content if b.type == "text")
            results = {r["id"]: r for r in json.loads(text)["articles"]}
        except (self.anthropic.APIError, RuntimeError, StopIteration, json.JSONDecodeError, KeyError) as e:
            log.warning("Claude classification failed (%s); falling back to rules for %d articles", e, len(articles))
            return [classify_rules(a) for a in articles]

        out = []
        for i, a in enumerate(articles):
            r = results.get(i)
            if r is None:
                out.append(classify_rules(a))
                continue
            evt = None if r["event_type"] == "None" else r["event_type"]
            out.append({
                **a,
                "tags": r["tags"][:3] or ["Other"],
                "event_type": evt,
                "event_confidence": r["confidence"] if evt else None,
                "event_rationale": r["rationale"] if evt else None,
                "people": [{"name": p["name"], "role": p["role"]} for p in r["people"][:4]],
                "classifier": "claude",
            })
        return out


def classify_all(articles):
    if not articles:
        return []
    if config.USE_CLAUDE:
        try:
            return ClaudeClassifier().classify(articles)
        except ImportError:
            log.warning("anthropic package not installed; using rules")
    return [classify_rules(a) for a in articles]
