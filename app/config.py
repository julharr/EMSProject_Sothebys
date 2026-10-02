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
# On startup, spend one request catching up on the last day of wealth stories
# (skipped if a catch-up already ran within the last 20 hours).
BACKFILL_ON_START = os.getenv("BACKFILL_ON_START", "true").lower() not in ("0", "false", "no")
# 48h because the free NewsAPI plan delays /everything results by about 24h.
BACKFILL_HOURS = int(os.getenv("BACKFILL_HOURS", "48"))
DB_PATH = Path(os.getenv("DB_PATH", ROOT / "signals.db"))

DEMO_MODE = not NEWSAPI_KEY
USE_CLAUDE = bool(ANTHROPIC_API_KEY)
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")

# Query used for /v2/everything pulls. NewsAPI caps q at 500 chars.
EVERYTHING_QUERY = (
    '(billionaire OR heir OR heiress OR tycoon OR mogul OR magnate OR dynasty OR "family fortune" OR collector) AND '
    '(dies OR died OR obituary OR divorce OR bankruptcy OR "chapter 11" OR debt OR estate OR inheritance OR '
    'auction OR "estate sale" OR succession OR lawsuit OR sells OR IPO)'
)
