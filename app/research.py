"""AI researcher: writes a specialist brief for one lead (or a pasted signal) with Claude.

Only runs when ANTHROPIC_API_KEY is set. The model sees the desk's own records for the
lead plus any related signals, and is told to use nothing else.
"""
import json
import logging

from . import config, desk, enrich

log = logging.getLogger(__name__)

RULES = """You are a senior research analyst on Sotheby's client development team. You turn public signals into briefs a specialist can act on.
Use only facts from the records below or from the user's message. Label any inference as an estimate. The records come from news coverage, so say what still needs confirming against the original public record.
For a death, divorce or debt situation, always recommend going through an intermediary (estate attorney, counsel, trust company or advisor) and never suggest contacting family directly.
Write the brief in plain text with these headings, each on its own line starting with "## ":
## Summary (two sentences)
## Why now
## Likely collecting interest
## Estimated value
## Recommended route (department, who reaches out, channel)
## First approach (two or three tactful sentences)
## Verify before contact (bullets starting with "- ")
## Sensitivity
Keep it under 350 words. Do not use tables or bold text."""


class ResearchUnavailable(Exception):
    pass


def _related(leads, words, exclude_id=None, limit=12):
    words = [w.lower() for w in words if len(w) > 2]
    out = []
    for l in leads:
        if l["id"] == exclude_id:
            continue
        for s in l["signals"]:
            hay = f"{s['title']} {s.get('summary') or ''} {l['name']} {l['region']}".lower()
            if any(w in hay for w in words):
                out.append({"date": s["d"], "type": s["type"], "title": s["title"], "source": s["src"],
                            "lead_id": l["id"], "lead_name": l["name"]})
    return out[:limit]


def brief(lead_id=None, signal_text=None):
    if not config.USE_CLAUDE:
        raise ResearchUnavailable("Add ANTHROPIC_API_KEY to your .env file and restart the server to turn on live research.")
    import anthropic

    leads = desk.build()
    if lead_id:
        lead = next((l for l in leads if l["id"] == lead_id), None)
        if not lead:
            raise ValueError("No lead with that id")
        cached = enrich.db.get_person(enrich.name_key(lead["name"])) or {}
        record = {**lead, "public_profile": {k: cached.get(k) for k in ("description", "bio", "born", "died",
                                                                         "occupations", "net_worth", "family")}
                  if cached.get("found") else "not looked up yet"}
        related = _related(leads, lead["name"].split(), exclude_id=lead_id)
        task = (f"Write a research brief for lead {lead_id}.\n\nLead file:\n{json.dumps(record, ensure_ascii=False)}"
                f"\n\nRelated signals on the desk:\n{json.dumps(related, ensure_ascii=False)}")
    else:
        related = _related(leads, signal_text.split())
        task = (f'A new public signal just came in:\n"""{signal_text}"""\n\nSignals on the desk that may match:\n'
                f"{json.dumps(related, ensure_ascii=False)}\n\nIf one matches an existing lead, say so. Then write the brief.")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    try:
        msg = client.beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            system=RULES,
            messages=[{"role": "user", "content": task}],
        )
    except anthropic.RateLimitError:
        raise ResearchUnavailable("Too many requests just now. Wait a minute, then run it again.")
    except anthropic.APIStatusError as e:
        log.warning("Research call failed: %s", e)
        raise ResearchUnavailable(f"Claude returned an error ({e.status_code}). Run it again.")
    except anthropic.APIConnectionError:
        raise ResearchUnavailable("Couldn't reach Claude. Check your internet connection.")
    if msg.stop_reason == "refusal":
        raise ResearchUnavailable("The analyst declined this request. Rephrase the signal and try again.")
    return "".join(b.text for b in msg.content if b.type == "text")
