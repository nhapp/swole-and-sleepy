import json
import os
from datetime import date, timedelta

from swole import crypto, metrics
from swole.demo import synth

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json")))
TODAY = date(2026, 10, 22)


def test_crypto_roundtrip():
    obj = {"a": [1, 2, 3], "b": "ü"}
    assert crypto.decrypt(crypto.encrypt(obj, "pw"), "pw") == obj


def test_crypto_wrong_passphrase_fails():
    env = crypto.encrypt({"x": 1}, "right")
    try:
        crypto.decrypt(env, "wrong")
    except Exception:
        return
    raise AssertionError("decrypt with wrong passphrase should fail")


def test_dedupe_prefers_garmin():
    g = {"id": "garmin:1", "source": "garmin", "start_utc": "2026-10-20T11:00:00+00:00", "duration_s": 3600}
    s_dup = dict(g, id="strava:9", source="strava", start_utc="2026-10-20T11:00:30+00:00")
    s_own = dict(g, id="strava:10", source="strava", start_utc="2026-10-20T18:00:00+00:00")
    ids = [a["id"] for a in metrics.dedupe_activities([s_dup, g, s_own])]
    assert ids == ["garmin:1", "strava:10"]


def test_trimp_increases_with_intensity():
    easy, _ = metrics.activity_load({"duration_s": 3600, "avg_hr": 130}, 185, 50, "male")
    hard, m = metrics.activity_load({"duration_s": 3600, "avg_hr": 165}, 185, 50, "male")
    assert m == "trimp" and hard > easy * 1.5


def test_calibrating_without_history():
    days = {(TODAY - timedelta(days=i)).isoformat(): {"hrv": 60, "rhr": 50, "sleep_s": 27000} for i in range(5)}
    rec = metrics.recovery(days, CFG, TODAY)
    assert rec["today"]["calibrating"] and rec["today"]["score"] is None


def test_stress_block_is_detected():
    raw = synth(TODAY, tz=CFG['timezone'])
    for i in range(7):  # a hard week: HRV down, resting HR up, every night
        raw["days"][(TODAY - timedelta(days=i)).isoformat()].update({"hrv": 42, "rhr": 59})
    out = metrics.compute(raw, CFG, TODAY)
    rec, tr = out["recovery"], out["training"]
    assert rec["today"]["status"] in ("moderate", "low")
    assert tr["today"]["acwr"] > 1.0
    titles = {f["title"] for f in rec["flags"]}
    assert "HRV below normal range" in titles or "Resting HR elevated" in titles
    # Mirrored Strava copies of Garmin runs must not double the load.
    assert all(a["source"] == "garmin" or a["sport"] == "Cycling" for a in tr["recent"])


def test_body_trend_and_rate():
    out = metrics.compute(synth(TODAY, tz=CFG['timezone']), CFG, TODAY)
    t = out["body"]["today"]
    assert -0.6 < t["rate_kg_wk"] < 0  # synthetic loss is ~0.25 kg/wk
    assert t["trend"] is not None and t["fat_change_kg"] < 0


def test_baseline_steady_state_scores_near_70():
    days = {(TODAY - timedelta(days=i)).isoformat(): {"hrv": 60 + (i % 3) - 1, "rhr": 50 + (i % 2), "sleep_s": 27000}
            for i in range(70)}
    rec = metrics.recovery(days, CFG, TODAY)
    assert 55 <= rec["today"]["score"] <= 85 and rec["today"]["status"] in ("good", "moderate")


def _ms(d: date, hhmm: str, tz=CFG["timezone"]):
    from datetime import datetime, time
    from zoneinfo import ZoneInfo
    return int(datetime.combine(d, time.fromisoformat(hhmm), ZoneInfo(tz)).timestamp() * 1000)


def test_sleep_proxy_uses_watch_off_and_on():
    wake = TODAY
    prev = wake - timedelta(days=1)
    days = {
        # Evening: short shower gap at 19:00 must be ignored; watch off at 22:30.
        prev.isoformat(): {"wear": [[_ms(prev, "06:40"), _ms(prev, "19:00")], [_ms(prev, "19:20"), _ms(prev, "22:30")]]},
        # Morning: first reading at 06:50.
        wake.isoformat(): {"wear": [[_ms(wake, "06:50"), _ms(wake, "12:00")]]},
    }
    p = metrics.sleep_proxy(days, wake, CFG)
    assert (p["off"], p["on"], p["asleep"], p["wake"]) == ("22:30", "06:50", "23:15", "06:45")
    assert p["sleep_s"] == (7 * 60 + 30) * 60


def test_sleep_proxy_handles_bedtime_after_midnight():
    wake = TODAY
    prev = wake - timedelta(days=1)
    days = {
        prev.isoformat(): {"wear": [[_ms(prev, "07:00"), _ms(prev, "23:59")]]},
        wake.isoformat(): {"wear": [[_ms(wake, "00:00"), _ms(wake, "00:40")], [_ms(wake, "07:30"), _ms(wake, "09:00")]]},
    }
    p = metrics.sleep_proxy(days, wake, CFG)
    assert (p["asleep"], p["wake"]) == ("01:25", "07:25")


def test_sleep_proxy_none_when_worn_overnight():
    wake = TODAY
    prev = wake - timedelta(days=1)
    days = {prev.isoformat(): {"wear": [[_ms(prev, "07:00"), _ms(prev, "23:59")]]},
            wake.isoformat(): {"wear": [[_ms(wake, "00:00"), _ms(wake, "09:00")]]}}
    assert metrics.sleep_proxy(days, wake, CFG) is None


def test_auto_sleep_source_prefers_garmin_then_proxy():
    wake = TODAY
    prev = wake - timedelta(days=1)
    days = {
        prev.isoformat(): {"wear": [[_ms(prev, "06:40"), _ms(prev, "22:30")]]},
        wake.isoformat(): {"wear": [[_ms(wake, "06:50"), _ms(wake, "12:00")]]},
    }
    s = metrics.recovery(days, CFG, TODAY)["series"][-1]
    assert s["sleep_src"] == "proxy" and s["sleep_h"] == 7.5
    days[wake.isoformat()]["sleep_s"] = 6 * 3600
    s = metrics.recovery(days, CFG, TODAY)["series"][-1]
    assert s["sleep_src"] == "garmin" and s["sleep_h"] == 6.0


def test_swims_and_lifts_use_garmin_load_not_wrist_hr():
    swim = {"sport": "Lap Swimming", "duration_s": 2700, "avg_hr": 95, "device_load": 70}
    lift = {"sport": "Strength Training", "duration_s": 2400, "avg_hr": 100}
    assert metrics.category(swim) == "swim" and metrics.category(lift) == "lift"
    assert metrics.activity_load(swim, 169, 52, CFG, "swim") == (70.0, "garmin")
    load, how = metrics.activity_load(lift, 169, 52, CFG, "lift")
    assert how == "duration" and load == 40 * 1.2


def test_long_run_and_max_hr_estimate():
    assert metrics.category({"sport": "Running", "duration_s": 95 * 60}) == "long_run"
    assert metrics.category({"sport": "Running", "duration_s": 45 * 60}) == "run"
    assert metrics.max_hr({"age": 56}) == 169 and metrics.max_hr({"age": 56, "max_hr": 175}) == 175


def test_targets_flex_by_day_type_protein_fixed():
    rest = metrics.targets_for([], CFG)
    long_day = metrics.targets_for(["lift", "long_run"], CFG)
    assert rest["kcal"] == 2100 and rest["carbs_g"] == 210
    assert long_day["kcal"] == 2750 and long_day["carbs_g"] == 370
    assert rest["protein_g"] == long_day["protein_g"] == 150


def test_nutrition_balance_and_protein_flag():
    days, food = {}, {}
    for i in range(1, 8):
        ds = (TODAY - timedelta(days=i)).isoformat()
        days[ds] = {"kcal_total": 2600}
        food[ds] = {"kcal": 2100, "protein_g": 110, "carbs_g": 250, "fat_g": 70}
    nu = metrics.nutrition(food, days, {}, CFG, TODAY)
    assert nu["today"]["balance7"] == -500 and nu["today"]["implied_lb_wk"] == -1.0
    assert "Protein under target" in {f["title"] for f in nu["flags"]}
    assert nu["today"]["so_far"]["balance"] is None  # today is incomplete


def test_body_fat_goal_implies_target_weight():
    out = metrics.compute(synth(TODAY, tz=CFG['timezone']), CFG, TODAY)
    t = out["body"]["today"]
    g = t["goal"]
    lean = out["body"]["series"][-1]["lean_trend"]
    assert g["fat_pct"] == 17.5 and abs(g["weight_kg"] - lean / 0.825) < 0.1
    assert t["ma7"] is not None


def test_mfp_csv_sums_meals_per_day(tmp_path):
    from swole.nutrition import parse_mfp_csv
    p = tmp_path / "mfp.csv"
    p.write_text("Date,Meal,Calories,Fat (g),Carbohydrates (g),Protein (g)\n"
                 "2026-11-12,Breakfast,500,15,60,30\n2026-11-12,Dinner,\"1,200\",40,110,70\n"
                 "11/13/2026,Lunch,700,20,80,45\n")
    days = parse_mfp_csv(str(p))
    assert days["2026-11-12"]["kcal"] == 1700 and days["2026-11-12"]["protein_g"] == 100
    assert days["2026-11-13"]["fat_g"] == 20


def test_garmin_row_finder_tolerates_nesting():
    from swole.garmin import _find, _rows, wear_segments
    resp = {"data": [{"calendarDate": "2026-11-12", "values": {"restingHeartRate": 52}},
                     {"calendarDate": "2026-11-13", "restingHeartRate": 51}]}
    rows = _rows(resp)
    assert sorted(r["calendarDate"] for r in rows) == ["2026-11-12", "2026-11-13"]
    assert {_find(r, "restingHeartRate") for r in rows} == {51, 52}
    hr = {"heartRateValues": [[0, 60], [120_000, 61], [240_000, None], [3_000_000, 58]]}
    assert wear_segments(hr) == [[0, 120_000], [3_000_000, 3_000_000]]


def test_paced_client_spaces_calls_and_waits_out_429():
    from garmin_personal_api import HttpError
    from swole.garmin import MIN_INTERVAL_S, PacedClient

    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(round(s, 2))
        now[0] += s

    class Fake:
        calls = 0

        def get_daily_heart_rate(self, d):
            Fake.calls += 1
            if Fake.calls == 2:
                raise HttpError(status_code=429, method="GET", url="u",
                                response_text='{"count":101,"limit":100,"retryInSeconds":50}')
            return {"ok": d}

    c = PacedClient(Fake(), sleep=sleep, clock=lambda: now[0])
    assert c.get_daily_heart_rate(1) == {"ok": 1}
    assert c.get_daily_heart_rate(2) == {"ok": 2}  # 429 on first try, then succeeds
    assert slept == [MIN_INTERVAL_S, 52.0]


def test_wear_backfill_resumes_after_interruption():
    from swole.run import wear_days
    cfg = dict(CFG, fetch={"lookback_days": 10, "wear_backfill_days": 90})
    # 60 old days already fetched (days 90..31 ago); the rest never were.
    raw = {"days": {(TODAY - timedelta(days=i)).isoformat(): {"wear": []} for i in range(31, 91)}}
    days = wear_days(raw, cfg, TODAY)
    assert days[0] == TODAY - timedelta(days=30) and days[-1] == TODAY and len(days) == 31
    # Fully fetched history: only the recent lookback window is re-pulled.
    raw = {"days": {(TODAY - timedelta(days=i)).isoformat(): {"wear": []} for i in range(0, 91)}}
    assert len(wear_days(raw, cfg, TODAY)) == 11


def test_garmin_daily_summary_v2_shape():
    from swole.garmin import SUMMARY_FIELDS, _find, _rows
    resp = {"data": [{"calendarDate": "2026-11-14", "movement": {"steps": 9000},
                      "calories": {"burnedResting": 1600, "burnedActive": 700, "burnedTotal": 2300},
                      "heartRate": {"minValue": 48, "maxValue": 150, "restingValue": 52}}]}
    row = _rows(resp)[0]
    got = {k: _find(row, *names) for k, names in SUMMARY_FIELDS.items()}
    assert got["rhr"] == 52 and got["kcal_total"] == 2300 and got["kcal_active"] == 700 and got["kcal_bmr"] == 1600
    assert got["steps"] == 9000


def test_backfill_reruns_for_mostly_missing_field():
    from swole.run import _mostly_missing
    cfg = dict(CFG, fetch={"backfill_days": 180, "lookback_days": 10, "wear_backfill_days": 90})
    days = {(TODAY - timedelta(days=i)).isoformat(): {"steps": 1} for i in range(1, 181)}
    assert _mostly_missing(days, ("rhr", "steps"), cfg, TODAY)
    for v in days.values():
        v["rhr"] = 50
    assert not _mostly_missing(days, ("rhr", "steps"), cfg, TODAY)
