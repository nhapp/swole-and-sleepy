"""Withings: OAuth2 refresh + body measurements (https://developer.withings.com/api-reference)."""

import time
from datetime import datetime, timezone
from urllib.parse import quote

import requests

AUTHORIZE_URL = "https://account.withings.com/oauth2_user/authorize2"
TOKEN_URL = "https://wbsapi.withings.net/v2/oauth2"
MEASURE_URL = "https://wbsapi.withings.net/measure"
SCOPE = "user.metrics"

# Withings measure type ids
WEIGHT_KG, FAT_FREE_KG, FAT_PCT, FAT_KG, MUSCLE_KG = 1, 5, 6, 8, 76
TYPES = {WEIGHT_KG: "weight_kg", FAT_FREE_KG: "fat_free_kg", FAT_PCT: "fat_pct", FAT_KG: "fat_kg", MUSCLE_KG: "muscle_kg"}


def authorize_url(client_id: str, redirect_uri: str) -> str:
    return (
        f"{AUTHORIZE_URL}?response_type=code&client_id={client_id}&scope={SCOPE}"
        f"&redirect_uri={quote(redirect_uri, safe='')}&state=swole"
    )


def _token_request(data: dict) -> dict:
    r = requests.post(TOKEN_URL, data={"action": "requesttoken", **data}, timeout=30)
    r.raise_for_status()
    j = r.json()
    if j.get("status") != 0:
        raise RuntimeError(f"Withings token error status={j.get('status')}: {j.get('error')}")
    b = j["body"]
    return {"access_token": b["access_token"], "refresh_token": b["refresh_token"], "expires_at": int(time.time()) + b["expires_in"]}


def exchange_code(client_id: str, client_secret: str, code: str, redirect_uri: str) -> dict:
    return _token_request(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
        }
    )


def ensure_fresh(tok: dict) -> dict:
    """Withings rotates the refresh token on every refresh; the caller must persist tok afterwards."""
    if tok.get("expires_at", 0) - time.time() > 600:
        return tok
    tok.update(
        _token_request(
            {
                "grant_type": "refresh_token",
                "client_id": tok["client_id"],
                "client_secret": tok["client_secret"],
                "refresh_token": tok["refresh_token"],
            }
        )
    )
    return tok


def fetch_measures(tok: dict, since: datetime, tz=timezone.utc) -> list[dict]:
    headers = {"Authorization": f"Bearer {tok['access_token']}"}
    data = {
        "action": "getmeas",
        "meastypes": ",".join(str(t) for t in TYPES),
        "category": 1,  # real measurements, not user objectives
        "startdate": int(since.timestamp()),
        "enddate": int(time.time()),
    }
    out = []
    while True:
        r = requests.post(MEASURE_URL, headers=headers, data=data, timeout=30)
        r.raise_for_status()
        j = r.json()
        if j.get("status") != 0:
            raise RuntimeError(f"Withings getmeas error status={j.get('status')}: {j.get('error')}")
        out.extend(normalize(g, tz) for g in j["body"].get("measuregrps", []))
        if not j["body"].get("more"):
            break
        data["offset"] = j["body"]["offset"]
    return [m for m in out if "weight_kg" in m or "fat_pct" in m]


def normalize(group: dict, tz=timezone.utc) -> dict:
    ts = datetime.fromtimestamp(group["date"], tz=tz)
    rec = {"id": f"withings:{group.get('grpid', group['date'])}", "ts": group["date"]}
    for m in group.get("measures", []):
        key = TYPES.get(m["type"])
        if key:
            rec[key] = round(m["value"] * 10 ** m["unit"], 3)
    rec["date"] = ts.date().isoformat()
    return rec
