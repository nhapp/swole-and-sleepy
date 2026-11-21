"""Garmin Connect via Garmin's official Personal API (garmin-connect-personal-api-python).

Auth is a long-lived personal API key (created in Garmin Connect settings), exchanged
each run for a 24-hour bearer token. Nothing rotates, so nothing needs persisting.

Response shapes are parsed defensively: rows are found by their `calendarDate` and
values by a list of candidate field names, so small schema differences don't break a run.
`python -m swole.run --probe` prints the field names each endpoint actually returns.
"""

import json
import os
import tempfile
import time
from datetime import date, datetime, timedelta, timezone

from garmin_personal_api import GarminPersonalApiClient, HttpError, PersonalApiAuthenticator

# Garmin allows ~100 requests a minute; stay under it and wait out a 429 when it happens anyway.
MIN_INTERVAL_S = 0.7
MAX_RATE_LIMIT_RETRIES = 4


class PacedClient:
    """Wraps GarminPersonalApiClient: spaces requests out and retries on HTTP 429."""

    def __init__(self, client: GarminPersonalApiClient, sleep=time.sleep, clock=time.monotonic):
        self._client, self._sleep, self._clock = client, sleep, clock
        self._last = float("-inf")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._client.close()

    def __getattr__(self, name):
        fn = getattr(self._client, name)
        if not callable(fn) or not (name.startswith("get") or name.startswith("search")):
            return fn

        def call(*args, **kwargs):
            for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
                wait = self._last + MIN_INTERVAL_S - self._clock()
                if wait > 0:
                    self._sleep(wait)
                self._last = self._clock()
                try:
                    return fn(*args, **kwargs)
                except HttpError as e:
                    if e.status_code != 429 or attempt == MAX_RATE_LIMIT_RETRIES:
                        raise
                    try:
                        retry = float(json.loads(e.response_text).get("retryInSeconds", 60))
                    except (ValueError, TypeError, AttributeError):
                        retry = 60.0
                    self._sleep(retry + 2)

        return call


def connect(api_key: str) -> PacedClient:
    cache = os.path.join(tempfile.gettempdir(), "swole-garmin-token.json")
    with PersonalApiAuthenticator(bearer_token_cache_file=cache) as auth:
        token = auth.obtain_token(api_key)
    return PacedClient(GarminPersonalApiClient(token.access_token))


def _find(d, *keys):
    """First non-null value for any of `keys`, searching nested dicts breadth-first."""
    queue = [d]
    while queue:
        cur = queue.pop(0)
        if not isinstance(cur, dict):
            continue
        for k in keys:
            v = cur.get(k)
            if v is not None and not isinstance(v, (dict, list)):
                return v
            if isinstance(v, dict) and v.get("value") is not None:
                return v["value"]
        queue.extend(v for v in cur.values() if isinstance(v, dict))
    return None


def _rows(resp) -> list[dict]:
    """Every dict carrying a `calendarDate`, wherever the endpoint nests them."""
    out, stack = [], [resp]
    while stack:
        cur = stack.pop()
        if isinstance(cur, list):
            stack.extend(cur)
        elif isinstance(cur, dict):
            if isinstance(cur.get("calendarDate"), str):
                out.append(cur)
            else:
                stack.extend(v for v in cur.values() if isinstance(v, (dict, list)))
    return out


def _chunks(start: date, end: date, days: int = 28):
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=days - 1), end)
        yield cur, stop
        cur = stop + timedelta(days=1)


# field candidates: {our key: (garmin field names...)}
SUMMARY_FIELDS = {
    "rhr": ("restingValue", "restingHeartRate", "restingHeartRateInBeatsPerMinute"),
    "steps": ("totalSteps", "steps"),
    "step_goal": ("dailyStepGoal", "stepGoal"),
    "kcal_total": ("burnedTotal", "totalKilocalories", "totalCalories"),
    "kcal_active": ("burnedActive", "activeKilocalories", "activeCalories"),
    "kcal_bmr": ("burnedResting", "bmrKilocalories", "bmrCalories"),
}
SLEEP_FIELDS = {
    "sleep_s": ("sleepTimeSeconds", "totalSleepTimeInSeconds", "durationInSeconds", "totalSleepSeconds"),
    "sleep_score": ("overall", "sleepScore", "overallScore"),
}


def fetch_daily(c: GarminPersonalApiClient, start: date, end: date) -> dict[str, dict]:
    """{date: {hrv, hrv_status, rhr, steps, step_goal, kcal_*, sleep_s, sleep_score}}."""
    days: dict[str, dict] = {}

    def put(d, key, value):
        if d and value is not None:
            days.setdefault(d[:10], {})[key] = value

    for a, b in _chunks(start, end):
        for row in _rows(c.get_daily_summaries(a, b)):
            for key, names in SUMMARY_FIELDS.items():
                put(row["calendarDate"], key, _find(row, *names))
        for row in _rows(c.get_daily_steps(a, b)):
            put(row["calendarDate"], "steps", _find(row, "totalSteps", "steps"))
            put(row["calendarDate"], "step_goal", _find(row, "stepGoal", "dailyStepGoal"))
        for row in _rows(c.get_daily_hrv_summaries(a, b)):
            put(row["calendarDate"], "hrv", _find(row, "lastNightAvg", "lastNightAverage"))
            put(row["calendarDate"], "hrv_status", _find(row, "status"))
        for row in _rows(c.get_daily_sleep_summaries(a, b)):
            for key, names in SLEEP_FIELDS.items():
                put(row["calendarDate"], key, _find(row, *names))
    return days


def wear_segments(hr_day, max_gap_ms: int = 10 * 60 * 1000) -> list[list[int]]:
    """Collapse a day's HR samples into [start_ms, end_ms] periods the watch was worn.

    Samples come every ~2 minutes while worn; a gap over `max_gap_ms` means it was off.
    """
    values = (hr_day or {}).get("heartRateValues") or [] if isinstance(hr_day, dict) else []
    ts = sorted(p[0] for p in values if isinstance(p, (list, tuple)) and len(p) > 1 and p[1])
    segs: list[list[int]] = []
    for t in ts:
        if segs and t - segs[-1][1] <= max_gap_ms:
            segs[-1][1] = t
        else:
            segs.append([t, t])
    return segs


def fetch_wear(c, days: list[date]):
    """Yield (date, wear segments) one day at a time, so progress survives a later failure."""
    for d in days:
        yield d.isoformat(), wear_segments(c.get_daily_heart_rate(d))


FOOD_FIELDS = {
    "kcal": ("calories",),
    "protein_g": ("protein", "proteinInGrams", "proteins"),
    "carbs_g": ("carbohydrates", "carbs", "carbohydratesInGrams"),
    "fat_g": ("fat", "fats", "fatInGrams", "totalFat"),
}


def food_day(resp) -> dict | None:
    """Daily food totals from Garmin's food log, which MyFitnessPal feeds when the accounts are linked.

    Today MFP passes only the calorie total; macros are read too in case Garmin starts carrying them.
    """
    content = (resp or {}).get("dailyNutritionContent") if isinstance(resp, dict) else None
    if not isinstance(content, dict) or not content.get("calories"):
        return None
    out = {k: content.get(names[0]) if names[0] in content else _find(content, *names) for k, names in FOOD_FIELDS.items()}
    return {k: round(v) for k, v in out.items() if isinstance(v, (int, float)) and v > 0} | {"source": "garmin-mfp"}


def fetch_food(c, days: list[date]):
    """Yield (date, totals or None) one day at a time."""
    for d in days:
        yield d.isoformat(), food_day(c.get_daily_food_logs(d))


def fetch_activities(c: GarminPersonalApiClient, start: date, end: date) -> list[dict]:
    out, offset = [], 0
    while True:
        page = c.search_activities(start=offset, limit=100, start_date=start, end_date=end) or []
        if isinstance(page, dict):  # tolerate a wrapped list
            page = next((v for v in page.values() if isinstance(v, list)), [])
        if not page:
            break
        out.extend(normalize_activity(a) for a in page)
        offset += len(page)
        if offset >= 2000:
            break
    return out


def normalize_activity(a: dict) -> dict:
    gmt = a.get("startTimeGMT") or a.get("startTimeLocal")
    start = datetime.strptime(gmt[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    sport = ((a.get("activityType") or {}).get("typeKey") or "workout").replace("_", " ").title()
    return {
        "id": f"garmin:{a['activityId']}",
        "source": "garmin",
        "date": (a.get("startTimeLocal") or gmt)[:10],
        "start_utc": start.isoformat(),
        "sport": sport,
        "name": a.get("activityName") or "",
        "duration_s": a.get("movingDuration") or a.get("duration") or 0,
        "elapsed_s": a.get("elapsedDuration") or a.get("duration") or 0,
        "distance_m": a.get("distance") or 0,
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "device_load": a.get("activityTrainingLoad"),
        "kcal": a.get("calories"),
    }


def probe(c: GarminPersonalApiClient, day: date) -> None:
    """Print the field names (never values) each endpoint returns, to tune the parsers."""
    a = day - timedelta(days=2)
    calls = {
        "daily_summaries": lambda: c.get_daily_summaries(a, day),
        "daily_steps": lambda: c.get_daily_steps(a, day),
        "hrv_summaries": lambda: c.get_daily_hrv_summaries(a, day),
        "sleep_summaries": lambda: c.get_daily_sleep_summaries(a, day),
        "daily_heart_rate": lambda: c.get_daily_heart_rate(day),
        "activities": lambda: c.search_activities(start=0, limit=2, start_date=a, end_date=day),
        "food_logs": lambda: c.get_daily_food_logs(day),
    }

    def shape(x, depth=0):
        if depth > 4:
            return "…"
        if isinstance(x, dict):
            return {k: shape(v, depth + 1) for k, v in list(x.items())[:40]}
        if isinstance(x, list):
            return [shape(x[0], depth + 1), f"({len(x)} items)"] if x else []
        if isinstance(x, (int, float)) and not isinstance(x, bool):
            # Zero vs non-zero says whether a field carries data, without revealing the value.
            return f"{type(x).__name__}({'0' if x == 0 else '>0' if x > 0 else '<0'})"
        return type(x).__name__

    for name, fn in calls.items():
        try:
            resp = fn()
            print(f"--- {name}: {len(_rows(resp))} dated rows\n{shape(resp)}")
        except Exception as e:  # noqa: BLE001
            print(f"--- {name}: ERROR {type(e).__name__}: {str(e)[:200]}")
