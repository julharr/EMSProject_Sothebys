"""The fixed vocabulary: topic tags for sorting, and wealth-event types to flag.

Both the keyword classifier and the Claude classifier are constrained to these
lists, so the UI filters always line up with what the backend produces.
"""

# Topic tags — every article gets 1-3 of these.
TAGS = {
    "Markets & Finance": [r"\bstocks?\b", r"\bshares\b", r"\binvestors?\b", r"\bhedge funds?\b", r"\bprivate equity\b",
                          r"\bwall street\b", r"\bbonds?\b", r"\binterest rates?\b", r"\bfed\b", r"\binflation\b", r"\btreasury\b", r"\bcrypto",
                          r"\bbitcoin\b", r"\bearnings\b", r"\bnasdaq\b", r"\bs&p\b", r"\bdow\b", r"\bbanks?\b"],
    "Business & Deals": [r"\bmerger\b", r"\bacqui(re|res|red|sition)\b", r"\bbuyout\b", r"\bipo\b", r"\bdeal\b",
                         r"\bceo\b", r"\bstartup\b", r"\bvaluation\b", r"\blayoffs?\b", r"\bstake\b", r"\bchairman\b"],
    "Technology": [r"\bai\b", r"\bartificial intelligence\b", r"\btech\b", r"\bsoftware\b", r"\bchips?\b",
                   r"\bsemiconductor", r"\bsilicon valley\b", r"\bapp\b", r"\brobot"],
    "Politics & Policy": [r"\belection\b", r"\bsenate\b", r"\bcongress\b", r"\bwhite house\b", r"\bgovernor\b",
                          r"\bparliament\b", r"\bminister\b", r"\btariffs?\b", r"\bwealth tax\b", r"\blegislation\b"],
    "Legal & Courts": [r"\blawsuit\b", r"\bsue[sd]?\b", r"\bcourt\b", r"\bjudge\b", r"\btrial\b", r"\bverdict\b",
                       r"\bindict", r"\bsettlement\b", r"\bprobate\b", r"\battorneys?\b"],
    "Real Estate": [r"\bmansion\b", r"\bpenthouse\b", r"\breal estate\b", r"\bproperty\b", r"\blisting\b",
                    r"\btownhouse\b", r"\bvilla\b", r"\bestate\b", r"\bhomes?\b", r"\bapartment\b"],
    "Art & Collectibles": [r"\bart\b", r"\bartwork", r"\bpaintings?\b", r"\bauction", r"\bsotheby", r"\bchristie",
                           r"\bcollect(or|ors|ion)\b", r"\bmuseum\b", r"\bgallery\b", r"\bjewel", r"\bdiamond",
                           r"\bwatch(es)?\b", r"\bwine\b", r"\bmasterpiece\b", r"\bsculpture\b"],
    "Luxury & Lifestyle": [r"\bluxury\b", r"\byachts?\b", r"\bprivate jet\b", r"\bfashion\b", r"\bcouture\b",
                           r"\bsocialite\b", r"\bgala\b", r"\bsupercar"],
    "Philanthropy": [r"\bdonat", r"\bphilanthrop", r"\bfoundation\b", r"\bendow", r"\bcharit", r"\bgiving pledge\b"],
    "Family & Society": [r"\bobituary\b", r"\bwedding\b", r"\bmarri", r"\bdivorce\b", r"\bheirs?\b", r"\bheiress\b",
                         r"\bfamily\b", r"\bdynasty\b", r"\bwidow"],
    "Crime & Investigations": [r"\barrest", r"\bfraud\b", r"\bcharged\b", r"\binvestigation\b", r"\bpolice\b",
                               r"\bscandal\b", r"\bponzi\b", r"\bembezzl"],
    "World": [r"\bukraine\b", r"\brussia\b", r"\bchina\b", r"\beurope", r"\bmiddle east\b", r"\bwar\b", r"\bstorms?\b", r"\bearthquake\b",
              r"\bunited nations\b", r"\bnato\b", r"\bindia\b", r"\bjapan\b"],
    "Sports": [r"\bnfl\b", r"\bnba\b", r"\bmlb\b", r"\bnhl\b", r"\bsoccer\b", r"\bfootball\b", r"\bteam owner\b",
               r"\bchampionship\b", r"\bolympi", r"\bgolf\b", r"\btennis\b"],
    "Entertainment & Media": [r"\bfilm\b", r"\bmovie\b", r"\bhollywood\b", r"\bmusic\b", r"\balbum\b", r"\bstreaming\b",
                              r"\bnetflix\b", r"\bcelebrit", r"\bactor\b", r"\bactress\b", r"\bsinger\b"],
    "Health & Science": [r"\bhealth\b", r"\bcancer\b", r"\bvaccine\b", r"\bdisease\b", r"\bstudy finds\b",
                         r"\bscientists?\b", r"\bmedical\b", r"\bhospitals?\b"],
}

# Wealth-changing events — the things a Sotheby's client team would act on.
# "strong" phrases are unambiguous on their own; "weak" ones need wealth context.
WEALTH_EVENTS = {
    "Death": {
        "color": "#78716c",
        "blurb": "Death of a principal — estates often consign collections within 6-24 months.",
        "strong": [r"\bobituary\b", r"\bdies at \d+", r"\bdead at \d+", r"\bpasse[sd] away\b", r"\bdied (on|at|peacefully)\b"],
        "weak": [r"\bdies\b", r"\bdied\b", r"\bdeath of\b", r"\bfuneral\b", r"\bmourn", r"\bremembered\b"],
        "negative": [r"\bdeath toll\b", r"\bkilled in\b", r"\bdies in (crash|attack|shooting)\b"],
    },
    "Divorce": {
        "color": "#9f1239",
        "blurb": "Divorce or separation — asset division frequently forces sales of art, jewelry and property.",
        "strong": [r"\bfiles? for divorce\b", r"\bdivorce settlement\b", r"\bprenup", r"\balimony\b", r"\bfinalize[sd]? (their |his |her )?divorce\b"],
        "weak": [r"\bdivorc", r"\bsplit(s|ting)? (up|from)\b", r"\bseparat(e|es|ed|ion)\b", r"\bestranged\b"],
        "negative": [r"\bseparation of powers\b"],
    },
    "Debt & Distress": {
        "color": "#b45309",
        "blurb": "Debt, bankruptcy or margin pressure — distressed owners sell trophy assets quickly.",
        "strong": [r"\bchapter (7|11)\b", r"\bfiles? for bankruptcy\b", r"\bbankrupt", r"\breceivership\b",
                   r"\bforeclos", r"\bmargin call\b", r"\binsolven", r"\bdefault(s|ed)? on\b"],
        "weak": [r"\bdebts?\b", r"\bcreditors?\b", r"\blenders?\b", r"\brestructur", r"\bliquidat", r"\bloan\b",
                 r"\bseiz(e|ed|ure)\b"],
        "negative": [r"\bnational debt\b", r"\bdebt ceiling\b", r"\bdebt limit\b", r"\bstudent debt\b", r"\bsovereign debt\b"],
    },
    "Estate & Succession": {
        "color": "#4338ca",
        "blurb": "Inheritance, wills, trusts or a family succession fight — wealth is changing hands.",
        "strong": [r"\binherit", r"\bprobate\b", r"\bexecutors?\b", r"\bsuccession (battle|fight|plan|feud)\b",
                   r"\bestate of (the )?late\b", r"\bfamily feud\b", r"\bheirs?\b", r"\bheiress\b"],
        "weak": [r"\bwill\b(?= (is|was|leaves|left|contest))", r"\btrusts?\b", r"\bsuccess(ion|or)\b", r"\bestate\b"],
        "negative": [],
    },
    "Collection & Property Sale": {
        "color": "#047857",
        "blurb": "A collection, estate or trophy property is coming to market — a direct consignment lead.",
        "strong": [r"\bestate sale\b", r"\bconsign", r"\bcollection (to be|will be|goes|heads) (sold|on sale|to auction|under the hammer)\b",
                   r"\bdeaccession", r"\bgoes? to auction\b", r"\bheads? to auction\b", r"\bunder the hammer\b",
                   r"\blists? (his|her|their)? ?(mansion|estate|penthouse|home|villa)\b"],
        "weak": [r"\bauction", r"\bsells? (his|her|their)\b", r"\bselling (his|her|their)\b", r"\bsold for \$",
                 r"\bfor sale\b", r"\bmansion\b", r"\bcollection\b"],
        "negative": [r"\bspectrum auction\b", r"\btreasury auction\b", r"\bbond auction\b"],
    },
    "Liquidity Event": {
        "color": "#0369a1",
        "blurb": "New money: IPO, company sale or big stake sale — a newly liquid potential buyer.",
        "strong": [r"\bipo\b", r"\bgoes? public\b", r"\bsells? (his|her|their) (company|stake|business|firm)\b",
                   r"\bacquired (by|for)\b", r"\b(agrees|agreed) to (buy|sell|acquire)\b", r"\bbuyout\b", r"\bwindfall\b"],
        "weak": [r"\bacqui(re|res|red|sition)\b", r"\bmerger\b", r"\bvaluation\b", r"\bstake\b", r"\bpayout\b", r"\bunicorn\b"],
        "negative": [],
    },
}

# Words that signal a story is about a wealthy person or family.
WEALTH_CONTEXT = [
    r"\bbillionaires?\b", r"\bmillionaires?\b", r"\btycoon", r"\bmogul", r"\bmagnate", r"\bheirs?\b", r"\bheiress",
    r"\bdynasty\b", r"\bfortune\b", r"\bfamily office\b", r"\bcollector", r"\bphilanthropist", r"\bsocialite",
    r"\bnet worth\b", r"\brichest\b", r"\bfounder\b", r"\bco-founder\b", r"\bchairman\b", r"\bchairwoman\b",
    r"\bheir to\b", r"\broyal\b", r"\bduke\b", r"\bduchess\b", r"\bprince(ss)?\b", r"\bcountess\b", r"\bbaron",
    r"\bhedge fund (manager|founder|billionaire)\b", r"\bwealthy\b", r"\bart collector\b", r"\binvestor\b",
    r"\bmansion\b", r"\bestate\b", r"\byacht\b",
]

TAG_NAMES = list(TAGS.keys()) + ["Other"]
EVENT_NAMES = list(WEALTH_EVENTS.keys())
CONFIDENCE_LEVELS = ["high", "medium", "low"]
