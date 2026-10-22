"""Garmin Connect via the unofficial `garminconnect` library (Garmin has no personal API).

Login with username/password + MFA happens once, locally (setup_auth.py). The daily job
only resumes from the saved DI token JSON, which the library refreshes as needed;
the refreshed token is returned so it can be persisted.
"""

from datetime import date, datetime, timedelta, timezone

from garminconnect import Garmin


def login_interactive(email: str, password: str) -> str:
    g = Garmin(email, password, prompt_mfa=lambda: input("Garmin MFA code: ").strip())
    g.login()
    return g.client.dumps()


def connect(token_json: str) -> Garmin:
    g = Garmin()
    g.login(token_json)
    return g


def dump_tokens(g: Garmin) -> str:
    return g.client.dumps()


def _find(d, *keys):
    """First non-null value for any of `keys`, searching nested dicts breadth-first.

    Garmin's response shapes drift; this keeps the parser tolerant of nesting changes.
    """
    queue = [d]
    while queue:
        cur = queue.pop(0)
        if not isinstance(cur, dict):
            continue
        for k in keys:
            v = cur.get(k)
            if v is not None and not isinstance(v, dict):
                return v
            if isinstance(v, dict) and "value" in v and v["value"] is not None:
                return v["value"]
        queue.extend(v for v in cur.values() if isinstance(v, dict))
    return None


def _chunks(start: date, end: date, days: int):
    cur = start
    while cur <= end:
        stop = min(cur + timedelta(days=days - 1), end)
        yield cur.isoformat(), stop.isoformat()
        cur = stop + timedelta(days=1)


def fetch_daily(g: Garmin, start: date, end: date) -> dict[str, dict]:
    """Per-day wellness: {date: {hrv, hrv_status, rhr, sleep_s, sleep_score, steps, step_goal}}."""
    days: dict[str, dict] = {}

    def put(d, key, value):
        if d and value is not None:
            days.setdefault(d, {})[key] = value

    for a, b in _chunks(start, end, 28):
        hrv = g.get_hrv_data_range(a, b) or {}
        for row in hrv.get("hrvSummaries") or []:
            put(row.get("calendarDate"), "hrv", row.get("lastNightAvg"))
            put(row.get("calendarDate"), "hrv_status", row.get("status"))

    for row in g.get_rhr_daily(start.isoformat(), end.isoformat()):
        put(row["calendarDate"], "rhr", row["value"])

    for row in g.get_sleep_daily(start.isoformat(), end.isoformat()):
        d = row.get("calendarDate")
        secs = _find(row, "totalSleepTimeInSeconds", "sleepTimeSeconds", "totalSleepSeconds")
        put(d, "sleep_s", secs)
        put(d, "sleep_score", _find(row, "sleepScore", "overall", "sleepScoreValue"))

    for row in g.get_daily_steps(start.isoformat(), end.isoformat()) or []:
        d = row.get("calendarDate")
        put(d, "steps", _find(row, "totalSteps", "steps"))
        put(d, "step_goal", _find(row, "stepGoal", "dailyStepGoal"))

    return days


def wear_segments(hr_day: dict, max_gap_ms: int = 10 * 60 * 1000) -> list[list[int]]:
    """Collapse a day's HR samples into [start_ms, end_ms] periods the watch was worn.

    Samples come every ~2 minutes while worn; a gap over `max_gap_ms` means it was off.
    """
    ts = sorted(t for t, bpm in (hr_day or {}).get("heartRateValues") or [] if bpm)
    segs: list[list[int]] = []
    for t in ts:
        if segs and t - segs[-1][1] <= max_gap_ms:
            segs[-1][1] = t
        else:
            segs.append([t, t])
    return segs


def fetch_wear(g: Garmin, start: date, end: date) -> dict[str, list[list[int]]]:
    """{date: wear segments}. One request per day, so callers keep the window short."""
    out = {}
    cur = start
    while cur <= end:
        out[cur.isoformat()] = wear_segments(g.get_heart_rates(cur.isoformat()))
        cur += timedelta(days=1)
    return out


def fetch_activities(g: Garmin, start: date, end: date) -> list[dict]:
    return [normalize_activity(a) for a in g.get_activities_by_date(start.isoformat(), end.isoformat())]


def normalize_activity(a: dict) -> dict:
    start = datetime.strptime(a["startTimeGMT"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    sport = ((a.get("activityType") or {}).get("typeKey") or "workout").replace("_", " ").title()
    return {
        "id": f"garmin:{a['activityId']}",
        "source": "garmin",
        "date": (a.get("startTimeLocal") or a["startTimeGMT"])[:10],
        "start_utc": start.isoformat(),
        "sport": sport,
        "name": a.get("activityName") or "",
        "duration_s": a.get("movingDuration") or a.get("duration") or 0,
        "distance_m": a.get("distance") or 0,
        "avg_hr": a.get("averageHR"),
        "max_hr": a.get("maxHR"),
        "device_load": a.get("activityTrainingLoad"),
    }
