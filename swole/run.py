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


def _window(raw_key: str, store: dict, cfg: dict, today, source: str | None = None):
    """Backfill on a source's first run, otherwise re-pull a short lookback so late syncs land."""
    have = store.get(raw_key)
    if source and have:
        # A source whose records all sit inside the lookback window has never had its history pulled.
        oldest = min((r.get("date", "9999") for r in have.values() if r.get("source") == source), default=None)
        have = oldest is not None and oldest < (today - timedelta(days=cfg["fetch"]["lookback_days"])).isoformat()
    days = cfg["fetch"]["lookback_days"] if have else cfg["fetch"]["backfill_days"]
    return today - timedelta(days=days)


def _mostly_missing(days: dict, keys, cfg: dict, today) -> bool:
    span = [(today - timedelta(days=i)).isoformat() for i in range(1, cfg["fetch"]["backfill_days"] + 1)]
    return any(sum(k in days.get(d, {}) for d in span) < len(span) / 2 for k in keys)


def per_day_window(raw: dict, cfg: dict, today, marker: str, backfill: int) -> list:
    """Days to fetch for a one-request-per-day endpoint: the recent lookback window (late syncs),
    plus any day in the backfill window not fetched yet, so an interrupted backfill resumes."""
    recent = cfg["fetch"]["lookback_days"]
    days = raw.get("days", {})
    out = []
    for i in range(backfill, -1, -1):
        d = today - timedelta(days=i)
        if i <= recent or marker not in days.get(d.isoformat(), {}):
            out.append(d)
    return out


def wear_days(raw: dict, cfg: dict, today) -> list:
    return per_day_window(raw, cfg, today, "wear", cfg["fetch"]["wear_backfill_days"])


def coverage(raw: dict) -> str:
    """How many days have each field: lets a log reader check parsing without seeing any values."""
    counts: dict[str, int] = {}
    for rec in raw.get("days", {}).values():
        for k in rec:
            counts[k] = counts.get(k, 0) + 1
    fields = ", ".join(f"{k} {n}" for k, n in sorted(counts.items()))
    from .metrics import category

    by: dict[str, int] = {}
    for a in raw.get("activities", {}).values():
        k = f"{a['source']} {category(a)}"
        by[k] = by.get(k, 0) + 1
    acts = ", ".join(f"{k} {n}" for k, n in sorted(by.items()))
    other: dict[str, int] = {}
    for a in raw.get("activities", {}).values():
        if category(a) == "other":
            other[a["sport"]] = other.get(a["sport"], 0) + 1
    if other:
        acts += "; other = " + ", ".join(f"{k} {n}" for k, n in sorted(other.items(), key=lambda x: -x[1]))
    return (f"days with: {fields or 'nothing'}; activities {len(raw.get('activities', {}))} ({acts}); "
            f"body readings {len(raw.get('body', {}))}; food days {len(raw.get('nutrition', {}))} "
            f"({sum('protein_g' in v for v in raw.get('nutrition', {}).values())} with protein)")


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

    garmin_food = [False]

    def do_garmin():
        with garmin.connect(os.environ[GARMIN_KEY_ENV]) as g:
            start = _window("days", raw, cfg, today)
            # A field that's mostly missing (new, or first parsed wrong) gets the full backfill again.
            if _mostly_missing(raw["days"], ("rhr", "steps", "kcal_total"), cfg, today):
                start = today - timedelta(days=cfg["fetch"]["backfill_days"])
            for d, rec in garmin.fetch_daily(g, start, today).items():
                raw["days"].setdefault(d, {}).update(rec)
            for a in garmin.fetch_activities(g, _window("activities", raw, cfg, today, "garmin"), today):
                raw["activities"][a["id"]] = a
            # Food totals that MyFitnessPal syncs into Garmin. A CSV import or the MFP API, which
            # carry macros, take precedence over these calorie-only rows.
            for d, food in garmin.fetch_food(g, per_day_window(raw, cfg, today, "food_checked", 30)):
                raw["days"].setdefault(d, {})["food_checked"] = True
                current = raw["nutrition"].get(d, {})
                if food and current.get("source", "garmin-mfp") == "garmin-mfp":
                    raw["nutrition"][d] = food
                    garmin_food[0] = True
            # Watch-wear needs one request per day. Fetch the recent days again (late syncs) plus any
            # day in the backfill window not fetched yet, so an interrupted backfill resumes next run.
            for d, segs in garmin.fetch_wear(g, per_day_window(raw, cfg, today, "wear", cfg["fetch"]["wear_backfill_days"])):
                raw["days"].setdefault(d, {})["wear"] = segs
        
    def do_strava():
        strava.ensure_fresh(tokens["strava"])
        since = datetime.combine(_window("activities", raw, cfg, today, "strava"), datetime.min.time(), tz)
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
    print("coverage " + coverage(raw))
    # Nutrition arrives by import (python -m swole.nutrition), not in this job; keep its last status.
    prev = store.get("meta", {}).get("sources", {})
    status["nutrition"] = prev.get("nutrition") or {"ok": False, "error": "not set up", "last_success": None}
    if garmin_food[0]:
        status["nutrition"] = {"ok": True, "error": None, "last_success": now, "via": "garmin"}
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
