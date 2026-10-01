"""Demo feed used when no NEWSAPI_KEY is set.

Every person, outlet and story here is FICTIONAL — it exists only so the design
and classifier can be exercised without spending API requests.
"""
from datetime import datetime, timedelta, timezone

from . import db
from .enrich import name_key


def _a(slug, title, desc, source, hours_ago):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(timespec="seconds")
    return {"url": f"https://example.com/demo/{slug}", "title": title, "description": desc,
            "source": source, "author": None, "image_url": None, "published_at": ts}


BATCHES = [
    [
        _a("halvorsen-dies", "Shipping billionaire and art collector Edvard Halvorsen dies at 91",
           "Edvard Halvorsen, whose family fortune was built on tanker fleets, assembled one of Europe's largest private collections of Nordic painting.",
           "The Ledger (demo)", 1.2),
        _a("castellane-divorce", "Hedge fund founder files for divorce after 22 years of marriage",
           "Marcus Castellane filed for divorce from Celine Castellane in Manhattan, with the couple's Upper East Side townhouse and art holdings expected to be contested.",
           "Metro Courier (demo)", 1.6),
        _a("fed-rates", "Fed signals rates will hold steady through year-end",
           "Policymakers said inflation was cooling but that the labor market remained tight.",
           "Financial Dispatch (demo)", 1.9),
        _a("storm-dies", "Three die as storm batters coastal towns",
           "Officials said the death toll could rise as crews reach cut-off communities.",
           "Metro Courier (demo)", 2.0),
        _a("aldana-chapter11", "Aldana Resorts files for Chapter 11 as founder faces margin call",
           "The hospitality group's founder, Rafael Aldana, pledged shares and several properties as collateral for personal loans, according to filings.",
           "Financial Dispatch (demo)", 2.2),
        _a("ai-chips", "Chipmakers rally as AI demand outpaces supply",
           "Semiconductor stocks led the Nasdaq higher for a third straight session.",
           "Financial Dispatch (demo)", 2.4),
    ],
    [
        _a("whitmore-estate", "Heirs of late industrialist battle over $4 billion estate",
           "The children of Harold Whitmore are contesting a revised will that leaves the bulk of the family trust to his second wife, Daphne Whitmore.",
           "The Ledger (demo)", 0.6),
        _a("okafor-ipo", "Payments startup Lumora prices IPO, making founder a billionaire",
           "Co-founder Adaeze Okafor holds a stake now valued at more than $2 billion after the company went public on the Nasdaq.",
           "Financial Dispatch (demo)", 0.7),
        _a("museum-gala", "Museum gala draws record donations for new wing",
           "Organizers said the evening raised $40 million from philanthropists and foundations.",
           "Metro Courier (demo)", 0.8),
        _a("senate-tariff", "Senate debates new tariff package",
           "Lawmakers remain divided ahead of a key vote next week.",
           "Metro Courier (demo)", 0.9),
        _a("debt-ceiling", "Treasury warns of debt ceiling deadline",
           "The department said extraordinary measures could be exhausted by spring.",
           "Financial Dispatch (demo)", 1.0),
    ],
    [
        _a("devereaux-auction", "Socialite's jewel collection heads to auction after estate sale announced",
           "The collection of the late Margaux Devereaux, including a 30-carat sapphire, will be sold this fall, her executor Julien Devereaux confirmed.",
           "The Ledger (demo)", 0.2),
        _a("penthouse-list", "Media mogul lists his Park Avenue penthouse for $95 million",
           "Victor Sterling, who sold his broadcasting group last year, is also said to be selling his yacht.",
           "Metro Courier (demo)", 0.3),
        _a("tennis-final", "Underdog wins tennis championship in straight sets",
           "The 19-year-old qualifier stunned the top seed in the final.",
           "Metro Courier (demo)", 0.35),
        _a("vaccine-study", "Study finds new vaccine cuts hospital visits by half",
           "Scientists said the results held across all age groups.",
           "Financial Dispatch (demo)", 0.4),
    ],
]

FAMILY = lambda *names: [{"name": n, "url": None} for n in names]  # noqa: E731

PROFILES = {
    "Edvard Halvorsen": dict(description="Norwegian shipping magnate and collector (fictional)", born="1934", died="2026",
                             occupations=["shipowner", "art collector"], net_worth="$6.2B (2025)",
                             bio="Edvard Halvorsen built Halvorsen Tankers into one of the largest independent fleets in the North Sea and spent five decades collecting Nordic Romantic painting.",
                             family={"spouses": FAMILY("Ingrid Halvorsen (d. 2019)"), "parents": FAMILY("Olav Halvorsen", "Sigrid Halvorsen"),
                                     "children": FAMILY("Kristian Halvorsen", "Astrid Halvorsen-Lund"), "siblings": []}),
    "Marcus Castellane": dict(description="American hedge fund manager (fictional)", born="1968", occupations=["hedge fund manager"],
                              net_worth="$3.1B (2025)",
                              bio="Founder of Castellane Capital, a macro fund, and a noted buyer of post-war American art.",
                              family={"spouses": FAMILY("Celine Castellane"), "parents": [], "children": FAMILY("Olivia Castellane", "Theo Castellane"), "siblings": []}),
    "Celine Castellane": dict(description="Arts patron and museum trustee (fictional)", born="1971", occupations=["philanthropist"],
                              bio="Trustee of a contemporary art museum and chair of its acquisitions committee.",
                              family={"spouses": FAMILY("Marcus Castellane"), "parents": [], "children": FAMILY("Olivia Castellane", "Theo Castellane"), "siblings": []}),
    "Rafael Aldana": dict(description="Hospitality entrepreneur (fictional)", born="1959", occupations=["businessperson"],
                          bio="Founded Aldana Resorts in 1988 and expanded it to 40 properties across the Caribbean.",
                          family={"spouses": FAMILY("Lucía Aldana"), "parents": [], "children": FAMILY("Mateo Aldana"), "siblings": []}),
    "Harold Whitmore": dict(description="American industrialist (fictional)", born="1931", died="2026", occupations=["industrialist"],
                            bio="Chairman of Whitmore Steel for four decades.",
                            family={"spouses": FAMILY("Eleanor Whitmore (d. 2004)", "Daphne Whitmore"), "parents": [],
                                    "children": FAMILY("James Whitmore", "Caroline Whitmore Hale", "Peter Whitmore"), "siblings": []}),
    "Adaeze Okafor": dict(description="Nigerian-American fintech founder (fictional)", born="1988", occupations=["entrepreneur"],
                          net_worth="$2.1B (2026)", bio="Co-founded Lumora, a cross-border payments company, in 2017.",
                          family={"spouses": [], "parents": FAMILY("Chukwuma Okafor", "Ngozi Okafor"), "children": [], "siblings": []}),
    "Margaux Devereaux": dict(description="French-American socialite (fictional)", born="1940", died="2026", occupations=["socialite", "collector"],
                              bio="A fixture of Paris and New York society known for her jewelry collection.",
                              family={"spouses": FAMILY("Henri Devereaux (d. 1998)"), "parents": [], "children": FAMILY("Julien Devereaux"), "siblings": []}),
    "Victor Sterling": dict(description="Media executive (fictional)", born="1955", occupations=["media proprietor"], net_worth="$4.8B (2025)",
                            bio="Sold Sterling Broadcasting in 2025 after thirty years at its helm.",
                            family={"spouses": FAMILY("Annika Sterling"), "parents": [], "children": FAMILY("Grace Sterling"), "siblings": []}),
}

_cursor = {"i": 0}


def seed_profiles():
    for name, p in PROFILES.items():
        db.put_person(name_key(name), {"name": name, "found": True, "image": None, "wikipedia_url": None,
                                       "source": "Demo data (fictional)", **{"died": None, "net_worth": None, **p}})


def next_batch():
    i = _cursor["i"]
    if i >= len(BATCHES):
        return None
    _cursor["i"] += 1
    return [dict(a) for a in BATCHES[i]]


def reset_cursor(n_already_loaded):
    _cursor["i"] = n_already_loaded
