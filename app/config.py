import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"


def _load_env_file(path):
    """Minimal .env reader (no dependency). Values in the file win over unset/empty env vars."""
    if not path.exists():
        return False
    raw = path.read_bytes()
    text = raw.decode("utf-16") if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else raw.decode("utf-8-sig", "replace")
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if val and not os.environ.get(key):
            os.environ[key] = val
    return True


ENV_FILE_FOUND = _load_env_file(ENV_FILE)

NEWSAPI_KEY = os.getenv("NEWSAPI_KEY", "").strip()
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

PULL_INTERVAL_MINUTES = int(os.getenv("PULL_INTERVAL_MINUTES", "30"))
DAILY_REQUEST_LIMIT = int(os.getenv("DAILY_REQUEST_LIMIT", "95"))
PULL_MODE = os.getenv("PULL_MODE", "alternate")  # top | everything | alternate
NEWSAPI_COUNTRY = os.getenv("NEWSAPI_COUNTRY", "us")
PORT = int(os.getenv("PORT", "5050"))
# On startup, catch up on the last BACKFILL_DAYS full days of wealth stories: one request per day
# (NewsAPI returns at most 100 articles per request). A day is fetched again only until it's complete.
BACKFILL_ON_START = os.getenv("BACKFILL_ON_START", "true").lower() not in ("0", "false", "no")
BACKFILL_DAYS = int(os.getenv("BACKFILL_DAYS", "7"))
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "signals.db"))

DEMO_MODE = not NEWSAPI_KEY
# Free sources only (NewsAPI free plan, Wikipedia/Wikidata, museum APIs): Claude is never called,
# even if ANTHROPIC_API_KEY is set. Set FREE_APIS_ONLY=false to allow the paid Claude features.
FREE_APIS_ONLY = os.getenv("FREE_APIS_ONLY", "true").lower() not in ("0", "false", "no")
USE_CLAUDE = bool(ANTHROPIC_API_KEY) and not FREE_APIS_ONLY
# Contact sent with every Wikipedia/Wikidata/museum request (Wikimedia asks automated tools for one).
# Set CONTACT to an email or URL you're happy to share; defaults to the project's GitHub page.
CONTACT = os.getenv("CONTACT", "https://github.com/julharr/EMSProject_Sothebys").strip()
# Forbes real-time billionaires via the community rtb-api (free, no key; github.com/komed3/rtb-api).
# The author plans a format change; to pin the older, frozen format use .../komed3/rtb-api/v1/api
RTB_ENABLED = os.getenv("RTB_ENABLED", "true").lower() not in ("0", "false", "no")
RTB_BASE = os.getenv("RTB_BASE", "https://raw.githubusercontent.com/komed3/rtb-api/main/api").strip()
# SEC EDGAR (free, no key): insider sales from Form 4 filings become buy signals.
# The SEC requires a User-Agent with contact details: set CONTACT to an email address.
EDGAR_ENABLED = os.getenv("EDGAR_ENABLED", "true").lower() not in ("0", "false", "no")
EDGAR_INTERVAL_MINUTES = int(os.getenv("EDGAR_INTERVAL_MINUTES", "10"))
EDGAR_MIN_SALE_USD = float(os.getenv("EDGAR_MIN_SALE_USD", "5000000"))
EDGAR_BACKFILL_DAYS = int(os.getenv("EDGAR_BACKFILL_DAYS", "3"))
# Harvard Art Museums API (free key from harvardartmuseums.org/collections/api). Optional.
HARVARD_API_KEY = os.getenv("HARVARD_API_KEY", "").strip()
# How many people the background profile builder looks up per pull (each is ~6 free API calls).
PROFILES_PER_PULL = int(os.getenv("PROFILES_PER_PULL", "25"))
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")

# Query used for /v2/everything pulls. NewsAPI caps q at 500 chars.
EVERYTHING_QUERY = (
    '(billionaire OR heir OR heiress OR tycoon OR mogul OR magnate OR dynasty OR "family fortune" OR collector) AND '
    '(dies OR died OR obituary OR divorce OR bankruptcy OR "chapter 11" OR debt OR estate OR inheritance OR '
    'auction OR "estate sale" OR succession OR lawsuit OR sells OR IPO)'
)
