"""The assumptions. Raw normalized data in, derived health signals out.

Everything the dashboard shows is computed here so the logic lives in one tested
place; every tunable number comes from config.json.
"""

import math
import statistics
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SERIES_DAYS = 120


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def _clamp(x, lo=0.0, hi=100.0):
    return max(lo, min(hi, x))


def _r(x, n=1):
    return None if x is None else round(x, n)


def _daterange(start: date, end: date):
    for i in range((end - start).days + 1):
        yield start + timedelta(days=i)


# ---------------------------------------------------------------------------
# Recovery / readiness
# ---------------------------------------------------------------------------


def _baseline(values: list[float], min_n: int, log: bool):
    vals = [math.log(v) if log else v for v in values if v]
    if len(vals) < min_n:
        return None
    return statistics.fmean(vals), statistics.stdev(vals) or 1e-6


def _merged_wear(days: dict, dates: list[date], max_gap_ms: int = 10 * 60 * 1000):
    """Wear segments across several days, merging pieces split at midnight. None if any day is missing."""
    segs = []
    for d in dates:
        w = days.get(d.isoformat(), {}).get("wear")
        if w is None:
            return None
        segs.extend(w)
    merged: list[list[int]] = []
    for a, b in sorted(segs):
        if merged and a - merged[-1][1] <= max_gap_ms:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged


def sleep_proxy(days: dict, wake_date: date, cfg: dict) -> dict | None:
    """Sleep estimated from when the watch comes off at night and goes back on in the morning.

    The night is the longest no-wear gap that starts and ends inside the night window
    (default 18:00 the evening before -> 14:00 on the wake date). Asleep = watch off +
    `asleep_after_off_min`; awake = first reading back on - `awake_before_on_min`.
    """
    sp = cfg["recovery"]["sleep_proxy"]
    tz = ZoneInfo(cfg["timezone"])
    segs = _merged_wear(days, [wake_date - timedelta(days=1), wake_date])
    if not segs:
        return None
    to_ms = lambda dt: int(dt.timestamp() * 1000)
    w0 = to_ms(datetime.combine(wake_date - timedelta(days=1), time.fromisoformat(sp["night_window"][0]), tz))
    w1 = to_ms(datetime.combine(wake_date, time.fromisoformat(sp["night_window"][1]), tz))
    gaps = [(a[1], b[0]) for a, b in zip(segs, segs[1:]) if w0 <= a[1] and b[0] <= w1]
    gaps = [g for g in gaps if g[1] - g[0] >= sp["min_gap_hours"] * 3600 * 1000]
    if not gaps:
        return None
    off, on = max(gaps, key=lambda g: g[1] - g[0])
    asleep = off + sp["asleep_after_off_min"] * 60 * 1000
    wake = on - sp["awake_before_on_min"] * 60 * 1000
    hhmm = lambda ms: datetime.fromtimestamp(ms / 1000, tz).strftime("%H:%M")
    return {"sleep_s": max(0, (wake - asleep) // 1000), "off": hhmm(off), "on": hhmm(on),
            "asleep": hhmm(asleep), "wake": hhmm(wake)}


def _component_score(z_good: float) -> float:
    """Baseline day (z=0) scores 70; each SD in the good direction moves 25 points."""
    return _clamp(70 + 25 * _clamp(z_good, -2.8, 1.2))


def recovery(days: dict, cfg: dict, today: date) -> dict:
    rc = cfg["recovery"]
    target_h = rc["sleep_target_hours"]
    n_base, min_n, k = rc["baseline_days"], rc["min_baseline_days"], rc["normal_range_sd"]

    def window(key, end: date, n: int, include_end: bool):
        start = end - timedelta(days=n - (1 if include_end else 0))
        stop = end if include_end else end - timedelta(days=1)
        return [days.get(x.isoformat(), {}).get(key) for x in _daterange(start, stop)]

    series = []
    for dt in _daterange(today - timedelta(days=SERIES_DAYS - 1), today):
        rec = days.get(dt.isoformat(), {})
        hrv, rhr = rec.get("hrv"), rec.get("rhr")
        proxy = sleep_proxy(days, dt, cfg)
        garmin_h = rec["sleep_s"] / 3600 if rec.get("sleep_s") else None
        proxy_h = proxy["sleep_s"] / 3600 if proxy else None
        src = rc.get("sleep_source", "auto")
        if src == "garmin" or (src == "auto" and garmin_h):
            sleep_h, sleep_src = garmin_h, "garmin" if garmin_h else None
        else:
            sleep_h, sleep_src = proxy_h, "proxy" if proxy_h else None

        hb = _baseline(window("hrv", dt, n_base, False), min_n, log=True)
        rb = _baseline(window("rhr", dt, n_base, False), min_n, log=False)
        hrv7 = _mean(window("hrv", dt, 7, True))
        rhr3 = _mean(window("rhr", dt, 3, True))

        comps = {}
        if hb and hrv:
            z = (math.log(hrv) - hb[0]) / hb[1]
            comps["hrv"] = {"value": hrv, "z": round(z, 2), "score": _component_score(z)}
        if rb and rhr:
            z = (rhr - rb[0]) / rb[1]
            comps["rhr"] = {"value": rhr, "z": round(z, 2), "score": _component_score(-z)}
        if sleep_h:
            dur_score = _clamp(100 - 25 * max(0.0, target_h - sleep_h))
            use_score = sleep_src == "garmin" and rec.get("sleep_score") is not None
            s = (dur_score + rec["sleep_score"]) / 2 if use_score else dur_score
            comps["sleep"] = {"value": round(sleep_h, 2), "score": s}

        wsum = sum(rc["weights"][c] for c in comps)
        score = sum(comps[c]["score"] * rc["weights"][c] for c in comps) / wsum if wsum else None
        # Without both physiological baselines the score is just a sleep score; don't present it as readiness.
        if "hrv" not in comps and "rhr" not in comps:
            score = None

        series.append(
            {
                "date": dt.isoformat(),
                "hrv": hrv,
                "hrv7": _r(hrv7),
                "hrv_lo": _r(math.exp(hb[0] - k * hb[1])) if hb else None,
                "hrv_hi": _r(math.exp(hb[0] + k * hb[1])) if hb else None,
                "rhr": rhr,
                "rhr3": _r(rhr3),
                "rhr_base": _r(rb[0]) if rb else None,
                "sleep_h": _r(sleep_h, 2),
                "sleep_src": sleep_src,
                "sleep_garmin_h": _r(garmin_h, 2),
                "sleep_proxy_h": _r(proxy_h, 2),
                "proxy": proxy,
                "sleep_score": rec.get("sleep_score"),
                "steps": rec.get("steps"),
                "steps7": _r(_mean(window("steps", dt, 7, True)), 0),
                "score": _r(score, 0),
                "components": {c: {kk: _r(vv, 2) for kk, vv in v.items()} for c, v in comps.items()},
            }
        )

    # "Today" is the latest day with any wellness data (watch may not have synced yet).
    latest = next((s for s in reversed(series) if s["hrv"] or s["rhr"] or s["sleep_h"]), series[-1])
    flags = []
    if latest["hrv7"] and latest["hrv_lo"] and latest["hrv7"] < latest["hrv_lo"]:
        flags.append(_flag("warning", "HRV below normal range",
                           f"7-day HRV average {latest['hrv7']:.0f} ms is under your normal range "
                           f"({latest['hrv_lo']:.0f}–{latest['hrv_hi']:.0f} ms). Typical causes: accumulated training "
                           "stress, illness, alcohol, poor sleep."))
    elif latest["hrv7"] and latest["hrv_hi"] and latest["hrv7"] > latest["hrv_hi"]:
        flags.append(_flag("info", "HRV above normal range",
                           "Usually positive adaptation; if paired with fatigue or heavy training it can also "
                           "signal parasympathetic overreaching."))
    if latest["rhr3"] and latest["rhr_base"] and latest["rhr3"] - latest["rhr_base"] >= rc["rhr_elevated_bpm"]:
        flags.append(_flag("warning", "Resting HR elevated",
                           f"3-day resting HR {latest['rhr3']:.0f} bpm is {latest['rhr3'] - latest['rhr_base']:.0f} bpm "
                           "above baseline — an early sign of illness, heat, dehydration or overreaching."))
    last7 = [s["sleep_h"] for s in series[-7:] if s["sleep_h"]]
    debt = sum(max(0.0, target_h - h) for h in last7)
    if debt >= rc["sleep_debt_flag_hours"]:
        flags.append(_flag("warning", "Sleep debt",
                           f"{debt:.1f} h short of your {target_h:g} h target over the last 7 nights."))

    ac = cfg.get("activity", {})
    goal = ac.get("steps_goal")
    # Today's count is partial, so judge movement on the 7 complete days before today.
    full7 = [s["steps"] for s in series[-8:-1] if s["steps"] is not None]
    steps_avg = _mean(full7)
    if goal and steps_avg is not None and len(full7) >= 5 and steps_avg < goal * ac.get("low_steps_pct", 60) / 100:
        flags.append(_flag("info", "Low daily movement",
                           f"Averaging {steps_avg:,.0f} steps over the last 7 days, under {ac.get('low_steps_pct', 60)}% "
                           f"of your {goal:,} goal. Easy walking helps recovery without adding training load."))

    score = latest["score"]
    status = None
    if score is not None:
        status = "good" if score >= 65 else "moderate" if score >= 45 else "low"
        if sum(f["level"] == "warning" for f in flags) >= 2:
            status = "low"

    return {
        "series": series,
        "today": {
            "date": latest["date"],
            "score": score,
            "status": status,
            "components": latest["components"],
            "sleep_debt_h": round(debt, 1),
            "steps_7d_avg": _r(steps_avg, 0),
            "calibrating": score is None,
        },
        "flags": flags,
    }


def _flag(level: str, title: str, detail: str) -> dict:
    return {"level": level, "title": title, "detail": detail}


# ---------------------------------------------------------------------------
# Training load
# ---------------------------------------------------------------------------


def dedupe_activities(acts: list[dict]) -> list[dict]:
    """Garmin usually auto-uploads to Strava. Keep the Garmin copy when start times match."""
    acts = [dict(a) for a in acts]
    garmin = [a for a in acts if a["source"] == "garmin"]
    starts = [(datetime.fromisoformat(a["start_utc"]), a["duration_s"]) for a in garmin]
    out = list(garmin)
    for a in acts:
        if a["source"] == "garmin":
            continue
        t = datetime.fromisoformat(a["start_utc"])
        dup = any(
            abs((t - gt).total_seconds()) <= 300 and abs(a["duration_s"] - gd) <= max(120, 0.2 * gd)
            for gt, gd in starts
        )
        if not dup:
            out.append(a)
    return sorted(out, key=lambda a: a["start_utc"])


def category(a: dict, long_run_minutes: float = 75) -> str:
    """swim / run / long_run / lift / cycle / other, from the provider's sport name."""
    s = (a.get("sport") or "").lower()
    if "swim" in s:
        return "swim"
    if "run" in s and "rowing" not in s:
        return "long_run" if (a.get("duration_s") or 0) / 60 >= long_run_minutes else "run"
    if any(k in s for k in ("strength", "weight", "lift", "hiit", "functional")):
        return "lift"
    if any(k in s for k in ("cycl", "ride", "bik", "spin")):
        return "cycle"
    return "other"


def max_hr(profile: dict) -> float:
    return profile.get("max_hr") or round(208 - 0.7 * profile.get("age", 40))


def activity_load(a: dict, hr_max: float, rest_hr: float, cfg_or_sex, cat: str = "other") -> tuple[float, str]:
    """Training load for one session, on the TRIMP scale.

    Wrist HR is unreliable in water and barely moves when lifting, so swims and lifts use
    Garmin's own load (or duration) first; everything else uses Banister TRIMP from average HR.
    """
    tc = cfg_or_sex.get("training", {}) if isinstance(cfg_or_sex, dict) else {}
    sex = cfg_or_sex.get("profile", {}).get("sex", "male") if isinstance(cfg_or_sex, dict) else cfg_or_sex
    mins = (a.get("duration_s") or 0) / 60
    per_min = tc.get("duration_load_per_min", {})
    by_duration = mins * per_min.get(cat if cat != "long_run" else "run", per_min.get("default", 1.3))
    if cat in tc.get("device_load_first_for", ["swim", "lift"]):
        if a.get("device_load"):
            return float(a["device_load"]), "garmin"
        return by_duration, "duration"
    if a.get("avg_hr") and hr_max > rest_hr:
        hrr = _clamp((a["avg_hr"] - rest_hr) / (hr_max - rest_hr), 0, 1)
        kexp = 1.92 if sex == "male" else 1.67
        return mins * hrr * 0.64 * math.exp(kexp * hrr), "trimp"
    if a.get("device_load"):
        return float(a["device_load"]), "garmin"
    return by_duration, "duration"


def day_types(acts: list[dict]) -> dict[str, list[str]]:
    """{date: sorted categories trained that day}; days with no activity are absent (= rest)."""
    out: dict[str, set] = {}
    for a in acts:
        if a["cat"] != "other":
            out.setdefault(a["date"], set()).add(a["cat"])
    return {d: sorted(v) for d, v in out.items()}


def training(activities: list[dict], days: dict, cfg: dict, today: date) -> dict:
    tc, prof = cfg["training"], cfg["profile"]
    acts = dedupe_activities(activities)
    rhrs = [days.get((today - timedelta(days=i)).isoformat(), {}).get("rhr") for i in range(30)]
    rhrs = [x for x in rhrs if x]
    rest_hr = statistics.median(rhrs) if rhrs else 60

    load_by_day: dict[str, float] = {}
    hr_max = max_hr(prof)
    for a in acts:
        a["cat"] = category(a, tc.get("long_run_minutes", 75))
        a["load"], a["load_method"] = activity_load(a, hr_max, rest_hr, cfg, a["cat"])
        a["load"] = round(a["load"], 1)
        load_by_day[a["date"]] = load_by_day.get(a["date"], 0) + a["load"]

    first = _d(acts[0]["date"]) if acts else today
    span_start = min(first, today - timedelta(days=SERIES_DAYS - 1))
    all_days = list(_daterange(span_start, today))
    loads = [load_by_day.get(d.isoformat(), 0.0) for d in all_days]
    na, nc = tc["acute_days"], tc["chronic_days"]

    daily = []
    for i, d in enumerate(all_days):
        acute = sum(loads[max(0, i - na + 1): i + 1]) / na
        chronic = sum(loads[max(0, i - nc + 1): i + 1]) / nc
        enough = (d - first).days + 1 >= nc
        acwr = acute / chronic if enough and chronic > 0 else None
        if d >= today - timedelta(days=SERIES_DAYS - 1):
            daily.append({"date": d.isoformat(), "load": round(loads[i], 1), "acute": round(acute, 1),
                          "chronic": round(chronic, 1), "acwr": _r(acwr, 2)})

    last7 = loads[-7:]
    sd7 = statistics.pstdev(last7) if len(last7) > 1 else 0
    monotony = (statistics.fmean(last7) / sd7) if sd7 > 0 else None
    strain = sum(last7) * monotony if monotony else None

    # Weekly totals, Monday-start, last 12 weeks.
    monday = today - timedelta(days=today.weekday())
    weeks = []
    for w in range(11, -1, -1):
        ws = monday - timedelta(weeks=w)
        we = ws + timedelta(days=6)
        wk = [a for a in acts if ws <= _d(a["date"]) <= we]
        by_sport: dict[str, float] = {}
        sessions: dict[str, int] = {}
        for a in wk:
            by_sport[a["sport"]] = by_sport.get(a["sport"], 0) + a["duration_s"] / 60
            c = "run" if a["cat"] == "long_run" else a["cat"]
            sessions[c] = sessions.get(c, 0) + 1
        weeks.append({
            "week_start": ws.isoformat(),
            "minutes": round(sum(a["duration_s"] for a in wk) / 60),
            "distance_km": round(sum(a["distance_m"] for a in wk) / 1000, 1),
            "load": round(sum(a["load"] for a in wk)),
            "count": len(wk),
            "by_sport": {k: round(v) for k, v in by_sport.items()},
            "sessions": sessions,
        })

    t = daily[-1]
    acwr = t["acwr"]
    zone = None
    flags = []
    if acwr is not None:
        if acwr > tc["acwr_danger"]:
            zone = "danger"
            flags.append(_flag("critical", "Load spike",
                               f"Acute:chronic ratio {acwr:.2f} — this week's load is well above what you're "
                               "adapted to. Injury and illness risk rise sharply here."))
        elif acwr > tc["acwr_high"]:
            zone = "high"
            flags.append(_flag("warning", "Ramping fast",
                               f"Acute:chronic ratio {acwr:.2f}. Fine for a planned build week; don't stack another."))
        elif acwr < tc["acwr_low"]:
            zone = "low"
            flags.append(_flag("info", "Under-loading",
                               f"Acute:chronic ratio {acwr:.2f}. Expected during a taper or rest week; "
                               "if prolonged, fitness will drift down."))
        else:
            zone = "optimal"
    if monotony and monotony > 2:
        flags.append(_flag("warning", "Monotonous week",
                           f"Training monotony {monotony:.1f} (>2). Too little variation between hard and easy days."))

    targets = tc.get("weekly_targets", {})
    this_week = weeks[-1]["sessions"]
    weekly_progress = [{"cat": c, "target": n, "done": this_week.get(c, 0)} for c, n in targets.items() if n]

    recent = [
        {k: a.get(k) for k in ("date", "source", "sport", "cat", "name", "duration_s", "distance_m", "avg_hr",
                                "load", "load_method", "kcal")}
        for a in reversed(acts[-12:])
    ]
    return {
        "daily": daily,
        "weeks": weeks,
        "today": {"acute": t["acute"], "chronic": t["chronic"], "acwr": acwr, "zone": zone,
                  "monotony": _r(monotony, 2), "strain": _r(strain, 0), "rest_hr_used": rest_hr,
                  "max_hr_used": hr_max},
        "weekly_progress": weekly_progress,
        "day_types": day_types(acts),
        "flags": flags,
        "recent": recent,
    }


# ---------------------------------------------------------------------------
# Body composition
# ---------------------------------------------------------------------------


def _slope_per_day(points: list[tuple[int, float]]):
    if len(points) < 4:
        return None
    xs, ys = zip(*points)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else None


def body(measures: list[dict], cfg: dict, today: date) -> dict:
    bc = cfg["body"]
    alpha = bc["trend_alpha"]
    # One reading per day: the earliest (most consistent: morning, pre-food).
    per_day: dict[str, dict] = {}
    for m in sorted(measures, key=lambda m: m["ts"]):
        if "weight_kg" in m:
            per_day.setdefault(m["date"], dict(m))
        elif m["date"] in per_day:
            per_day[m["date"]].update({k: v for k, v in m.items() if k not in per_day[m["date"]]})

    series, trend, fat_trend, lean_trend, pct_trend = [], None, None, None, None
    ma_days = bc.get("moving_avg_days", 7)
    for ds in sorted(per_day):
        m = per_day[ds]
        w = m["weight_kg"]
        fat_kg = m.get("fat_kg") or (w * m["fat_pct"] / 100 if m.get("fat_pct") else None)
        lean = w - fat_kg if fat_kg else None
        trend = w if trend is None else trend + alpha * (w - trend)
        if fat_kg:
            fat_trend = fat_kg if fat_trend is None else fat_trend + alpha * (fat_kg - fat_trend)
            lean_trend = lean if lean_trend is None else lean_trend + alpha * (lean - lean_trend)
            pct_trend = fat_trend / trend * 100
        lo = (_d(ds) - timedelta(days=ma_days - 1)).isoformat()
        ma = _mean([per_day[x]["weight_kg"] for x in per_day if lo <= x <= ds])
        series.append({
            "date": ds, "weight": w, "trend": round(trend, 2), "ma7": _r(ma, 2), "fat_pct_trend": _r(pct_trend, 2),
            "fat_pct": m.get("fat_pct"), "fat_kg": _r(fat_kg, 2), "lean_kg": _r(lean, 2),
            "fat_trend": _r(fat_trend, 2), "lean_trend": _r(lean_trend, 2),
            "muscle_kg": m.get("muscle_kg"),
        })

    if not series:
        return {"series": [], "today": None, "flags": []}

    cutoff = today - timedelta(days=bc["slope_days"])
    window = [s for s in series if _d(s["date"]) >= cutoff]
    slope = _slope_per_day([((_d(s["date"]) - cutoff).days, s["trend"]) for s in window])
    last = series[-1]
    rate_kg_wk = slope * 7 if slope is not None else None
    rate_pct_wk = rate_kg_wk / last["trend"] * 100 if rate_kg_wk is not None else None

    def change(key):
        pts = [s for s in window if s.get(key) is not None]
        return round(pts[-1][key] - pts[0][key], 2) if len(pts) >= 2 else None

    fat_change, lean_change = change("fat_trend"), change("lean_trend")
    flags = []
    if rate_pct_wk is not None and -rate_pct_wk > bc["max_loss_pct_per_week"]:
        flags.append(_flag("warning", "Losing fast",
                           f"Trend weight falling {abs(rate_pct_wk):.1f}%/week. Above ~{bc['max_loss_pct_per_week']:g}%/week "
                           "the share of loss coming from muscle tends to rise."))
    if rate_pct_wk is not None and rate_pct_wk > bc["max_gain_pct_per_week"]:
        flags.append(_flag("info", "Gaining fast",
                           f"Trend weight rising {rate_pct_wk:.1f}%/week — more than lean gain alone typically explains."))
    if lean_change is not None and lean_change < -0.5 and (rate_kg_wk or 0) < 0:
        flags.append(_flag("warning", "Lean mass slipping",
                           f"Smoothed lean mass down {abs(lean_change):.1f} kg over {bc['slope_days']} days while losing "
                           "weight. Consider protein intake and strength work."))

    goal = None
    goal_w = bc.get("goal_weight_kg")
    goal_pct = bc.get("goal_fat_pct")
    if goal_pct and last.get("lean_trend"):
        # Weight at the goal body-fat % if lean mass holds steady.
        goal_w = last["lean_trend"] / (1 - goal_pct / 100)
    if goal_w:
        to_go = last["trend"] - goal_w
        eta = None
        if rate_kg_wk and to_go * rate_kg_wk < 0:
            eta = (today + timedelta(weeks=abs(to_go / rate_kg_wk))).isoformat()
        goal = {"weight_kg": round(goal_w, 1), "to_go_kg": round(to_go, 1), "eta": eta,
                "fat_pct": goal_pct, "fat_pct_now": last.get("fat_pct_trend")}

    recent_cut = today - timedelta(days=SERIES_DAYS - 1)
    return {
        "series": [s for s in series if _d(s["date"]) >= recent_cut],
        "today": {
            "date": last["date"], "weight": last["weight"], "trend": last["trend"], "ma7": last["ma7"],
            "fat_pct_trend": last["fat_pct_trend"],
            "fat_pct": last["fat_pct"], "rate_kg_wk": _r(rate_kg_wk, 2), "rate_pct_wk": _r(rate_pct_wk, 2),
            "fat_change_kg": fat_change, "lean_change_kg": lean_change, "goal": goal,
        },
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# Nutrition
# ---------------------------------------------------------------------------


def targets_for(types: list[str], cfg: dict) -> dict:
    """Daily targets: protein fixed; calories and carbs flex with the day's biggest demand."""
    nc = cfg["nutrition"]
    base = dict(nc["base_targets"])
    adj_table = nc.get("day_type_adjust", {})
    adjs = [adj_table.get(t, {}) for t in (types or ["rest"])]
    adj = max(adjs, key=lambda a: a.get("kcal", 0)) if adjs else {}
    base["kcal"] += adj.get("kcal", 0)
    base["carbs_g"] += adj.get("carbs_g", 0)
    return base


def nutrition(food: dict, days: dict, types_by_day: dict, cfg: dict, today: date) -> dict:
    nc = cfg["nutrition"]
    series = []
    for dt in _daterange(today - timedelta(days=SERIES_DAYS - 1), today):
        ds = dt.isoformat()
        f = food.get(ds, {})
        types = types_by_day.get(ds, [])
        tgt = targets_for(types, cfg)
        kcal_out = days.get(ds, {}).get("kcal_total")
        complete = dt < today
        logged = f.get("kcal") is not None
        series.append({
            "date": ds, "types": types or (["rest"] if complete else []),
            "kcal": f.get("kcal"), "protein_g": f.get("protein_g"), "carbs_g": f.get("carbs_g"), "fat_g": f.get("fat_g"),
            "t_kcal": tgt["kcal"], "t_protein_g": tgt["protein_g"], "t_carbs_g": tgt["carbs_g"], "t_fat_g": tgt["fat_g"],
            "kcal_out": kcal_out,
            # Intake minus Garmin's total burn (BMR + active). An estimate: both sides carry error.
            "balance": round(f["kcal"] - kcal_out) if logged and kcal_out and complete else None,
            "complete": complete,
        })

    done = [x for x in series if x["complete"]]
    last7 = done[-7:]
    logged7 = [x for x in last7 if x["kcal"] is not None]
    avg = lambda k: _r(_mean([x[k] for x in logged7]), 0)
    bal7 = [x["balance"] for x in last7 if x["balance"] is not None]
    avg_balance = _mean(bal7)
    implied_lb_wk = avg_balance * 7 / nc.get("kcal_per_lb", 3500) if avg_balance is not None and len(bal7) >= 4 else None
    protein_pct = (
        _mean([x["protein_g"] / x["t_protein_g"] * 100 for x in logged7 if x["protein_g"] is not None]) if logged7 else None
    )

    flags = []
    if food and len(logged7) < nc.get("min_logged_days_of_7", 5):
        flags.append(_flag("info", "Food log has gaps",
                           f"{len(logged7)} of the last 7 days logged. Averages and the calorie balance need most days."))
    if protein_pct is not None and protein_pct < nc.get("protein_flag_pct", 90):
        flags.append(_flag("warning", "Protein under target",
                           f"7-day average {avg('protein_g'):.0f} g/day, {protein_pct:.0f}% of your "
                           f"{nc['base_targets']['protein_g']} g target. That matters most when losing fat and lifting."))
    if avg_balance is not None and len(bal7) >= 4 and avg_balance < -nc.get("deficit_flag_kcal", 750):
        flags.append(_flag("warning", "Deep calorie deficit",
                           f"Estimated {abs(avg_balance):,.0f} kcal/day below Garmin's burn over the last week. "
                           "Large deficits make it harder to hold lean mass and recover from training."))

    return {
        "series": series,
        "today": {
            "date": today.isoformat(), "so_far": series[-1],
            "yesterday": done[-1] if done else None,
            "avg7": {"kcal": avg("kcal"), "protein_g": avg("protein_g"), "carbs_g": avg("carbs_g"), "fat_g": avg("fat_g"),
                     "logged_days": len(logged7)},
            "protein_pct7": _r(protein_pct, 0),
            "balance7": _r(avg_balance, 0),
            "implied_lb_wk": _r(implied_lb_wk, 2),
        },
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# Today's call
# ---------------------------------------------------------------------------


def guidance(rec: dict, tr: dict) -> dict:
    status = rec["today"]["status"]
    zone = tr["today"]["zone"]
    if status is None:
        return {"status": "calibrating", "headline": "Building your baseline",
                "detail": "Readiness needs about two weeks of HRV and resting-HR data before it means anything."}
    if status == "low" or zone == "danger":
        return {"status": "low", "headline": "Prioritise recovery",
                "detail": "Rest, or keep it easy — Zone 1–2, under 45 minutes. Sleep is the highest-leverage thing today."}
    if status == "moderate" or zone == "high":
        return {"status": "moderate", "headline": "Keep it moderate",
                "detail": "Aerobic work is fine; hold the intervals and heavy lifts for a fresher day."}
    if zone == "low":
        return {"status": "good", "headline": "Fresh and under-loaded",
                "detail": "Good day for a key session, or to start building volume back up."}
    return {"status": "good", "headline": "Green light",
            "detail": "Recovered and load is in balance — a good day for a quality session."}


def compute(raw: dict, cfg: dict, today: date) -> dict:
    days = raw.get("days", {})
    rec = recovery(days, cfg, today)
    tr = training(list(raw.get("activities", {}).values()), days, cfg, today)
    bd = body(list(raw.get("body", {}).values()), cfg, today)
    nu = nutrition(raw.get("nutrition", {}), days, tr["day_types"], cfg, today)

    # Reality check: does the logged calorie balance match what the scale trend says?
    implied = nu["today"]["implied_lb_wk"]
    measured = bd["today"]["rate_kg_wk"] * 2.20462 if bd["today"] and bd["today"]["rate_kg_wk"] is not None else None
    nu["today"]["measured_lb_wk"] = _r(measured, 2)
    if implied is not None and measured is not None and abs(implied - measured) > 0.5:
        nu["flags"].append(_flag("info", "Calorie balance and scale disagree",
                                 f"Logged intake vs Garmin's burn implies {implied:+.1f} lb/week; your weight trend shows "
                                 f"{measured:+.1f} lb/week. Usually unlogged food or an over-estimated burn. Trust the scale trend."))
    return {"recovery": rec, "training": tr, "body": bd, "nutrition": nu, "guidance": guidance(rec, tr)}
