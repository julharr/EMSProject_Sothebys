"""The pull loop: every PULL_INTERVAL_MINUTES, spend exactly one NewsAPI request."""
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import classifier, config, db, demo_data, newsapi_client

log = logging.getLogger(__name__)
_pull_lock = threading.Lock()


def next_mode():
    if config.PULL_MODE in ("top", "everything"):
        return config.PULL_MODE
    return "everything" if db.last_regular_mode() == "top" else "top"


def run_pull(manual=False, demo_minutes_ago=0, mode=None):
    """Do one pull. Returns a status dict. Never exceeds the rolling 24h quota."""
    if not _pull_lock.acquire(blocking=False):
        return {"status": "busy"}
    try:
        if config.DEMO_MODE:
            return _demo_pull(demo_minutes_ago)

        used = db.requests_last_24h()
        if used >= config.DAILY_REQUEST_LIMIT:
            return {"status": "quota", "message": f"{used}/{config.DAILY_REQUEST_LIMIT} requests used in the last 24h"}

        mode = mode or next_mode()
        pull_id = db.start_pull(mode)
        try:
            fetched = newsapi_client.fetch(mode)
        except Exception as e:  # network, auth, rate-limit — record and move on
            log.error("NewsAPI pull failed: %s", e)
            db.finish_pull(pull_id, "error", error=str(e))
            return {"status": "error", "message": str(e)}

        seen = db.known_urls([a["url"] for a in fetched])
        fresh = [a for a in fetched if a["url"] not in seen]
        now = db.now_iso()
        for a in fresh:
            a["fetched_at"] = now
        inserted = db.insert_articles(classifier.classify_all(fresh), pull_id)
        db.finish_pull(pull_id, "ok", n_returned=len(fetched), n_new=len(inserted))
        log.info("Pull %s (%s): %d returned, %d new", pull_id, mode, len(fetched), len(inserted))
        return {"status": "ok", "mode": mode, "returned": len(fetched), "new": len(inserted)}
    finally:
        _pull_lock.release()


def _demo_pull(minutes_ago=0):
    batch = demo_data.next_batch()
    if batch is None:
        return {"status": "ok", "mode": "demo", "returned": 0, "new": 0, "message": "Demo feed exhausted"}
    pull_id = db.start_pull("demo", counts_against_quota=False)
    now = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds")
    for a in batch:
        a["fetched_at"] = now
    inserted = db.insert_articles(classifier.classify_all(batch), pull_id)
    db.finish_pull(pull_id, "demo", n_returned=len(batch), n_new=len(inserted))
    return {"status": "ok", "mode": "demo", "returned": len(batch), "new": len(inserted)}


def next_pull_at():
    last = db.last_pull()
    if not last:
        return datetime.now(timezone.utc)
    started = datetime.fromisoformat(last["started_at"])
    return started + timedelta(minutes=config.PULL_INTERVAL_MINUTES)


def _loop():
    while True:
        wait = (next_pull_at() - datetime.now(timezone.utc)).total_seconds()
        if wait > 0:
            time.sleep(min(wait, 60))
            continue
        result = run_pull()
        if result.get("status") == "quota":
            time.sleep(15 * 60)  # wait for the rolling window to free up
        # A failed pull is still recorded, so the next attempt waits a full interval.


def backfill_days(now=None):
    """The full UTC days to catch up on, oldest first, skipping any already fetched completely."""
    now = now or datetime.now(timezone.utc)
    due = []
    for back in range(config.BACKFILL_DAYS, 0, -1):
        day = (now - timedelta(days=back)).date()
        last = db.last_pull_of_mode(f"backfill:{day}")
        if last and last["status"] == "ok":
            started = datetime.fromisoformat(last["started_at"])
            day_end = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) + timedelta(days=1)
            # The free plan delays search results ~24h, so a day is only complete once fetched 24h after it ended.
            if started >= day_end + timedelta(hours=24) or now - started < timedelta(hours=20):
                continue
        due.append(day)
    return due


def backfill_if_needed():
    """On startup, fetch each missing day of the last week: one request per day."""
    if config.DEMO_MODE or not config.BACKFILL_ON_START:
        return []
    days = backfill_days()
    if not days:
        log.info("Catch-up: the last %d days are already loaded", config.BACKFILL_DAYS)
    results = []
    for day in days:
        result = run_pull(mode=f"backfill:{day}")
        log.info("Catch-up for %s: %s", day, result)
        results.append(result)
        if result.get("status") in ("quota", "busy"):
            break
    return results


def _start():
    backfill_if_needed()
    _loop()


def start_scheduler():
    t = threading.Thread(target=_start, name="pull-scheduler", daemon=True)
    t.start()
    return t
