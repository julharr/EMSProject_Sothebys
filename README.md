# Wealth Signal Monitor

A local web app that pulls news headlines from NewsAPI every 30 minutes, tags every story, and flags
**wealth-changing events** — the "3 Ds" (death, divorce, debt) plus the events around them — that an
auction-house client team would want to know about. Click any story to see the factor that triggered
it, plus a short bio and the immediate family (spouse, parents, children) of each key person.

```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # add NEWSAPI_KEY (and optionally ANTHROPIC_API_KEY)
python server.py              # open http://localhost:5050
```

With no `NEWSAPI_KEY` it runs in **demo mode** with a fictional feed, so you can review the design
without spending requests. "Pull now" simulates the next pull.

## How it works

```
 every 30 min ──► NewsAPI (1 request) ──► dedupe by URL ──► classify ──► SQLite ──► table UI (polls every 60s)
                                                              │                         │ click a row
                                              keyword rules or Claude          Wikipedia + Wikidata lookup
                                              (tags, event, people)            (bio, spouse, parents, children; cached)
```

| File | What it does |
|---|---|
| `server.py` | Flask app + JSON API; starts the pull scheduler |
| `app/pipeline.py` | Scheduler loop; one NewsAPI request per pull; hard 24h quota guard |
| `app/newsapi_client.py` | The NewsAPI call and response cleanup |
| `app/taxonomy.py` | **The fixed tag list and wealth-event definitions — edit this to tune** |
| `app/classifier.py` | Keyword classifier (free) and Claude classifier (optional, much better) |
| `app/enrich.py` | Person profiles from Wikipedia/Wikidata, cached in SQLite |
| `static/` | The single-page UI |

### Request budget (100/day on the free plan)

- Each pull is exactly **one** request. At 30-minute intervals that's 48/day.
- `DAILY_REQUEST_LIMIT` (default 95) is enforced over a rolling 24 hours, including "Pull now" clicks
  and failed requests. The header shows the meter.
- `PULL_MODE=alternate` (default) switches between `/top-headlines` (general US news) and
  `/everything` with a wealth-event query (`EVERYTHING_QUERY` in `app/config.py`). The second one is
  where most signals come from; general headlines alone rarely mention private wealth.

### Wealth events

| Event | Examples |
|---|---|
| Death | "dies at 91", obituary, passed away |
| Divorce | files for divorce, prenup, alimony, separation |
| Debt & Distress | Chapter 11, bankruptcy, margin call, foreclosure, creditors |
| Estate & Succession | inheritance, heirs, probate, will contests, succession fights |
| Collection & Property Sale | estate sale, consignment, "heads to auction", trophy home listed |
| Liquidity Event | IPO, founder sells company or stake — new money, a potential buyer |

Each flag gets a confidence level. **High** = the event and a wealthy principal are both explicit
("billionaire collector dies"); **medium** = one is inferred; **low** = keyword only. The table
defaults to medium & high. Things like "death toll", "debt ceiling" and "national debt" are excluded.

### Classifiers

- **Keyword rules** (default, free): regex over title + description. Good at catching events; weaker
  at telling a wealthy principal from anyone else, and at pulling out names (it skips Title-Case
  headlines and relies on the description).
- **Claude** (set `ANTHROPIC_API_KEY`): one batched call per pull, constrained by a JSON schema to
  the same tags and event types. It also names each person's role in the story ("deceased",
  "spouse filing for divorce"). Only new articles are sent. Rough cost on the default
  `claude-opus-5-5` is about $0.10–0.20 per pull, so roughly $3–10/day; set
  `CLAUDE_MODEL=claude-sonnet-5-5` to cut that about in half. If a call fails, that batch falls back to the rules.

### People profiles

When you open a story, each named person is looked up on Wikipedia, checked against Wikidata to make
sure it's a human (not a company with the same name), and these fields come back: summary bio, birth
and death dates, occupation, net worth (when Wikidata has it), spouses, parents, children and siblings.
Results are cached, so each name is looked up only once. Private individuals usually won't have a
page, and the panel says so.

## Caveats

- **NewsAPI's free Developer plan** is licensed for development and testing only, and its
  `/everything` results may be delayed (24h on the free tier at time of writing).
  Check your plan terms before using this for business.
- Wikipedia/Wikidata family data is only as complete as the public record.
- Classification is a triage aid. Verify a story before acting on it.

## Tests

```
python -m unittest discover tests
```
