"""Daily job: refresh tokens, pull new data, recompute signals, write encrypted files.

    SWOLE_PASSPHRASE=... python -m swole.run
"""

import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import crypto, garmin, metrics, strava, withings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKENS_PATH = os.path.join(ROOT, "state", "tokens.enc.json")
DATA_PATH = os.path.join(ROOT, "site", "data.enc.json")
CONFIG_PATH = os.path.join(ROOT, "config.json")
GARMIN_KEY_ENV = "GARMIN_PERSONAL_API_KEY"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def passphrase() -> str:
    p = os.environ.get("SWOLE_PASSPHRASE")
    if not p:
        sys.exit("SWOLE_PASSPHRASE is not set")
    return p


def _window(raw_key: str, store: dict, cfg: dict, today):
    """Backfill on first run, otherwise re-pull a short lookback so late syncs land."""
    days = cfg["fetch"]["lookback_days"] if store.get(raw_key) else cfg["fetch"]["backfill_days"]
    return today - timedelta(days=days)


def wear_days(raw: dict, cfg: dict, today) -> list:
    recent = cfg["fetch"]["lookback_days"]
    backfill = cfg["fetch"]["wear_backfill_days"]
    days = raw.get("days", {})
    out = []
    for i in range(backfill, -1, -1):
        d = today - timedelta(days=i)
        if i <= recent or "wear" not in days.get(d.isoformat(), {}):
            out.append(d)
    return out


def coverage(raw: dict) -> str:
    """How many days have each field: lets a log reader check parsing without seeing any values."""
    counts: dict[str, int] = {}
    for rec in raw.get("days", {}).values():
        for k in rec:
            counts[k] = counts.get(k, 0) + 1
    fields = ", ".join(f"{k} {n}" for k, n in sorted(counts.items()))
    return f"days with: {fields or 'nothing'}; activities {len(raw.get('activities', {}))}"


def pull(tokens: dict, store: dict, cfg: dict, today, tz) -> dict:
    """Fetch every configured source; one failing source never blocks the others."""
    status = {}
    raw = store.setdefault("raw", {})
    for k in ("days", "activities", "body", "nutrition"):
        raw.setdefault(k, {})
    now = datetime.now(timezone.utc).isoformat()

    def run(name, fn):
        prev = store.get("meta", {}).get("sources", {}).get(name, {})
        if name not in tokens and not (name == "garmin" and os.environ.get(GARMIN_KEY_ENV)):
            status[name] = {"ok": False, "error": "not set up", "last_success": prev.get("last_success")}
            return
        try:
            fn()
            status[name] = {"ok": True, "error": None, "last_success": now}
        except Exception as e:  # noqa: BLE001 - surface any provider failure on the dashboard
            traceback.print_exc()
            status[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300],
                            "last_success": prev.get("last_success")}

    def do_garmin():
        with garmin.connect(os.environ[GARMIN_KEY_ENV]) as g:
            start = _window("days", raw, cfg, today)
            for d, rec in garmin.fetch_daily(g, start, today).items():
                raw["days"].setdefault(d, {}).update(rec)
            for a in garmin.fetch_activities(g, _window("activities", raw, cfg, today), today):
                raw["activities"][a["id"]] = a
            # Watch-wear needs one request per day. Fetch the recent days again (late syncs) plus any
            # day in the backfill window not fetched yet, so an interrupted backfill resumes next run.
            for d, segs in garmin.fetch_wear(g, wear_days(raw, cfg, today)):
                raw["days"].setdefault(d, {})["wear"] = segs
            print("garmin   " + coverage(raw))

    def do_strava():
        strava.ensure_fresh(tokens["strava"])
        since = datetime.combine(_window("activities", raw, cfg, today), datetime.min.time(), tz)
        for a in strava.fetch_activities(tokens["strava"], since):
            raw["activities"][a["id"]] = a

    def do_withings():
        withings.ensure_fresh(tokens["withings"])
        since = datetime.combine(_window("body", raw, cfg, today), datetime.min.time(), tz)
        for m in withings.fetch_measures(tokens["withings"], since, tz):
            raw["body"][m["id"]] = m

    run("garmin", do_garmin)
    run("strava", do_strava)
    run("withings", do_withings)
    # Nutrition arrives by import (python -m swole.nutrition), not in this job; keep its last status.
    prev = store.get("meta", {}).get("sources", {})
    status["nutrition"] = prev.get("nutrition") or {"ok": False, "error": "not set up", "last_success": None}
    return status


def build(store: dict, cfg: dict, status: dict, today) -> dict:
    store["derived"] = metrics.compute(store["raw"], cfg, today)
    store["meta"] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "today": today.isoformat(),
        "sources": status,
        "config": cfg,
    }
    return store


def main() -> int:
    cfg = load_config()
    if "--probe" in sys.argv:
        with garmin.connect(os.environ[GARMIN_KEY_ENV]) as g:
            garmin.probe(g, datetime.now(ZoneInfo(cfg["timezone"])).date())
        return 0
    pw = passphrase()
    tz = ZoneInfo(cfg["timezone"])
    today = datetime.now(tz).date()

    tokens = crypto.read_file(TOKENS_PATH, pw, default={})
    store = crypto.read_file(DATA_PATH, pw, default={})
    try:
        status = pull(tokens, store, cfg, today, tz)
    finally:
        # Strava and Withings rotate refresh tokens; losing a fresh one means re-running setup.
        crypto.write_file(TOKENS_PATH, tokens, pw)

    crypto.write_file(DATA_PATH, build(store, cfg, status, today), pw)
    for name, s in status.items():
        print(f"{name:9s} {'ok' if s['ok'] else 'not connected' if s['error'] == 'not set up' else 'FAILED: ' + s['error']}")
    g = store["derived"]["guidance"]
    print(f"Today: {g['headline']} — {g['detail']}")
    failed = [n for n, s in status.items() if n != "nutrition" and not s["ok"] and s["error"] != "not set up"]
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
