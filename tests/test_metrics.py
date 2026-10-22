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
    out = metrics.compute(synth(TODAY), CFG, TODAY)
    rec, tr = out["recovery"], out["training"]
    assert rec["today"]["status"] in ("moderate", "low")
    assert tr["today"]["acwr"] > 1.0
    titles = {f["title"] for f in rec["flags"]}
    assert "HRV below normal range" in titles or "Resting HR elevated" in titles
    # Mirrored Strava copies of Garmin runs must not double the load.
    assert all(a["source"] == "garmin" or a["sport"] == "Cycling" for a in tr["recent"])


def test_body_trend_and_rate():
    out = metrics.compute(synth(TODAY), CFG, TODAY)
    t = out["body"]["today"]
    assert -0.6 < t["rate_kg_wk"] < 0  # synthetic loss is ~0.25 kg/wk
    assert t["trend"] is not None and t["fat_change_kg"] < 0


def test_baseline_steady_state_scores_near_70():
    days = {(TODAY - timedelta(days=i)).isoformat(): {"hrv": 60 + (i % 3) - 1, "rhr": 50 + (i % 2), "sleep_s": 27000}
            for i in range(70)}
    rec = metrics.recovery(days, CFG, TODAY)
    assert 55 <= rec["today"]["score"] <= 85 and rec["today"]["status"] in ("good", "moderate")


def _ms(d: date, hhmm: str, tz="America/New_York"):
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
