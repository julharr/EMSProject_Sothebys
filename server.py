"""Wealth Signal Monitor — local web app.

    python server.py            # then open http://localhost:5050
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_from_directory

from app import config, db, demo_data, enrich, pipeline
from app.taxonomy import EVENT_NAMES, TAG_NAMES, WEALTH_EVENTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = Flask(__name__, static_folder="static", static_url_path="/static")
_pool = ThreadPoolExecutor(max_workers=4)


@app.get("/")
def index():
    return send_from_directory("static", "index.html")


@app.get("/api/meta")
def meta():
    return jsonify({
        "tags": TAG_NAMES,
        "events": [{"name": n, "color": WEALTH_EVENTS[n]["color"], "blurb": WEALTH_EVENTS[n]["blurb"]} for n in EVENT_NAMES],
    })


@app.get("/api/status")
def status():
    last = db.last_pull()
    return jsonify({
        "demo": config.DEMO_MODE,
        "classifier": "claude" if config.USE_CLAUDE else "rules",
        "interval_minutes": config.PULL_INTERVAL_MINUTES,
        "requests_24h": db.requests_last_24h(),
        "request_limit": config.DAILY_REQUEST_LIMIT,
        "last_pull": last,
        "next_pull_at": pipeline.next_pull_at().isoformat(timespec="seconds"),
        "server_time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })


@app.get("/api/articles")
def articles():
    since = request.args.get("since_id", 0, type=int)
    return jsonify(db.list_articles(since_id=since))


@app.get("/api/articles/<int:article_id>")
def article_detail(article_id):
    a = db.get_article(article_id)
    if not a:
        return jsonify({"error": "not found"}), 404
    people = list(_pool.map(lambda p: enrich.profile(p["name"], p.get("role")), a["people"]))
    evt = WEALTH_EVENTS.get(a["event_type"]) if a["event_type"] else None
    return jsonify({**a, "profiles": people, "event_blurb": evt["blurb"] if evt else None,
                    "event_color": evt["color"] if evt else None})


@app.post("/api/pull")
def pull_now():
    return jsonify(pipeline.run_pull(manual=True))


def explain_config():
    """Say plainly where settings came from, so 'why am I in demo mode?' answers itself."""
    if not config.ENV_FILE_FOUND:
        others = sorted(p.name for p in config.ROOT.glob(".env*") if p.name != ".env.example")
        hint = f" Found {', '.join(others)} instead — rename it to exactly .env" if others else \
            " Create it with:  cp .env.example .env   (Windows: copy .env.example .env)"
        logging.warning("No .env file at %s.%s", config.ENV_FILE, hint)
    elif not config.NEWSAPI_KEY:
        logging.warning(".env found at %s but NEWSAPI_KEY is empty. Put your key after the = sign.", config.ENV_FILE)
    else:
        k = config.NEWSAPI_KEY
        logging.info("NewsAPI key loaded from .env (%s…%s)", k[:4], k[-4:])


def bootstrap():
    explain_config()
    db.init()
    if config.DEMO_MODE:
        demo_data.seed_profiles()
        loaded = db.pull_count()
        demo_data.reset_cursor(loaded)
        if loaded == 0:  # pre-load two "earlier" pulls; leave one for the Pull-now button
            pipeline.run_pull(demo_minutes_ago=60)
            pipeline.run_pull(demo_minutes_ago=30)
        logging.info("DEMO MODE — no NEWSAPI_KEY set. Data is fictional. Click 'Pull now' to simulate a pull.")
    else:
        pipeline.start_scheduler()
        logging.info("Live mode: pulling every %d min (classifier: %s)",
                     config.PULL_INTERVAL_MINUTES, "claude" if config.USE_CLAUDE else "rules")


def port_in_use(port):
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


if __name__ == "__main__":
    import sys
    import threading
    import webbrowser

    if port_in_use(config.PORT):
        sys.exit(f"\n  Port {config.PORT} is already in use — an older copy of this server is probably still running.\n"
                 f"  Close that terminal window (or press Ctrl+C in it), then run python server.py again.\n")
    bootstrap()
    url = f"http://localhost:{config.PORT}"
    print(f"\n  Wealth Signal Monitor running at {url}  (Ctrl+C to stop)\n", flush=True)
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=config.PORT, debug=False, use_reloader=False)
