"""Synthetic data run through the real pipeline -> site/demo.json (no personal data).

    python -m swole.demo
"""

import json
import math
import os
import random
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import metrics
from .run import ROOT, load_config


def synth(today: date, n_days: int = 200, seed: int = 7, tz: str = "America/New_York") -> dict:
    rnd = random.Random(seed)
    zone = ZoneInfo(tz)
    days, acts, body, food = {}, {}, {}, {}
    worn_nights = set()
    night = {}  # wake date -> (watch worn overnight?, hours asleep)
    # Ends near the spec's baseline: 165 lb, ~20.7% body fat.
    weight, fat_pct = 78.0, 22.8
    for i in range(n_days):
        d = today - timedelta(days=n_days - 1 - i)
        ds = d.isoformat()
        # A build block in the last ~10 days pushes load up and HRV down.
        stress = 1.0 if i > n_days - 10 else 0.0
        hrv = rnd.gauss(58 - 9 * stress, 5)
        rhr = rnd.gauss(52 + 4 * stress, 1.6)
        sleep_h = max(4.5, rnd.gauss(7.3 - 0.6 * stress, 0.6))
        steps = max(1500, rnd.gauss(9200 if d.weekday() < 5 else 11000, 2200))
        days[ds] = {"rhr": round(rhr), "steps": int(steps), "step_goal": 8000}
        # Most nights the watch comes off; ~35% of nights it's worn and Garmin records sleep + HRV.
        worn = rnd.random() < 0.35
        if worn:
            worn_nights.add(ds)
            days[ds].update({"hrv": round(hrv), "sleep_s": int(sleep_h * 3600),
                             "sleep_score": int(max(40, min(95, rnd.gauss(78 - 8 * stress, 7))))})
        night[ds] = (worn, sleep_h)

        wd = d.weekday()
        # (sport, minutes, avg HR, Garmin load, km/min); Mon swim+lift, Tue run, Wed lift, Thu swim,
        # Fri rest, Sat run (some weeks a ride), Sun long run.
        plan = {0: [("Lap Swimming", 45, 128, 70, 0.045), ("Strength Training", 40, 108, 35, 0)],
                1: [("Running", 50, 148, None, 0.17)], 2: [("Strength Training", 45, 110, 40, 0)],
                3: [("Lap Swimming", 50, 130, 78, 0.046)], 5: [("Running", 45, 150, None, 0.17)],
                6: [("Running", 95, 142, None, 0.165)]}
        sessions = list(plan.get(wd, []))
        if wd == 5 and rnd.random() < 0.3:
            sessions = [("Cycling", 100, 132, None, 0.42)]
        burn = 0
        for k, (sport, mins, hr, dload, kmpm) in enumerate(sessions):
            if rnd.random() < 0.1:
                continue
            mins *= 1 + 0.35 * stress
            src = "strava" if sport == "Cycling" else "garmin"
            start = datetime.combine(d, datetime.min.time(), timezone.utc) + timedelta(hours=11 + k)
            kcal = mins * (10 if sport != "Strength Training" else 6)
            burn += kcal
            key = f"{src}:{i}{k}"
            acts[key] = {
                "id": key, "source": src, "date": ds, "start_utc": start.isoformat(), "sport": sport,
                "name": f"{sport} session", "duration_s": int(mins * 60), "distance_m": mins * kmpm * 1000,
                "avg_hr": round(rnd.gauss(hr, 4)), "max_hr": hr + 20,
                "device_load": round(dload * (1 + 0.35 * stress) * rnd.uniform(0.85, 1.15)) if dload else None,
                "kcal": round(kcal),
            }
            if src == "garmin":  # mirror to Strava, as auto-sync would; must dedupe
                acts[f"strava:m{i}{k}"] = dict(acts[key], id=f"strava:m{i}{k}", source="strava",
                                               start_utc=(start + timedelta(seconds=20)).isoformat())
        days[ds]["kcal_total"] = round(1580 + steps * 0.035 + burn)
        # Food log: ~85% of days, close to target, protein a little light.
        if d < today and rnd.random() < 0.85:
            target = 2350 + (400 if wd == 6 else -250 if wd == 4 else 0)
            kcal = rnd.gauss(target - 120, 180)
            prot = rnd.gauss(138, 14)
            fat = rnd.gauss(72, 10)
            food[ds] = {"kcal": round(kcal), "protein_g": round(prot), "fat_g": round(fat),
                        "carbs_g": round(max(80, (kcal - prot * 4 - fat * 9) / 4)), "source": "demo"}

        weight += -0.022 + rnd.gauss(0, 0.02)
        fat_pct += -0.011
        if rnd.random() < 0.8:
            w = weight + rnd.gauss(0, 0.45) + 0.4 * math.sin(i / 3)
            ts = int(datetime.combine(d, datetime.min.time(), timezone.utc).timestamp()) + 7 * 3600
            fp = fat_pct + rnd.gauss(0, 0.6)
            body[f"withings:{i}"] = {"id": f"withings:{i}", "ts": ts, "date": ds, "weight_kg": round(w, 2),
                                     "fat_pct": round(fp, 1), "fat_kg": round(w * fp / 100, 2),
                                     "muscle_kg": round(w * (1 - fp / 100) * 0.95, 2)}
    # Wear segments per calendar day (epoch ms). Night N ends on wake date N.
    for ds in days:
        d = date.fromisoformat(ds)
        mid = int(datetime.combine(d, datetime.min.time(), zone).timestamp() * 1000)
        at = lambda minutes: mid + int(minutes) * 60000
        r = random.Random(ds)
        worn_before, _ = night.get(ds, (True, 7))
        nxt = (d + timedelta(days=1)).isoformat()
        worn_after, sleep_next = night.get(nxt, (True, 7))
        on_min = 0 if worn_before else r.gauss(6 * 60 + 40, 18)
        # Put it back on ~5 min after waking tomorrow; take it off ~45 min before falling asleep tonight.
        wake_next = r.gauss(6 * 60 + 35, 18) + 24 * 60
        off_min = 24 * 60 - 1 if worn_after else min(24 * 60 - 1, wake_next - sleep_next * 60 - 45)
        shower = r.gauss(19 * 60, 40)
        segs = [[at(on_min), at(shower)], [at(shower + 20), at(off_min)]]
        if d == today:
            segs = [[at(on_min), at(on_min + 150)]]
        days[ds]["wear"] = segs
    return {"days": days, "activities": acts, "body": body, "nutrition": food}


def main():
    cfg = load_config()
    today = date.today()
    raw = synth(today)
    sources = {s: {"ok": True, "error": None, "last_success": datetime.now(timezone.utc).isoformat()}
               for s in ("garmin", "strava", "withings", "nutrition")}
    out = {"derived": metrics.compute(raw, cfg, today),
           "meta": {"generated_at": datetime.now(timezone.utc).isoformat(), "today": today.isoformat(),
                    "sources": sources, "config": cfg, "demo": True}}
    path = os.path.join(ROOT, "site", "demo.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"wrote {path}: {out['derived']['guidance']['headline']}")


if __name__ == "__main__":
    main()
