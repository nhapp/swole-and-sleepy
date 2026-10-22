"""Strava: OAuth2 refresh + activity list (https://developers.strava.com/docs/reference/)."""

import time
from datetime import datetime, timezone

import requests

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API = "https://www.strava.com/api/v3"
SCOPE = "read,activity:read_all"


def authorize_url(client_id: str, redirect_uri: str) -> str:
    return (
        f"{AUTHORIZE_URL}?client_id={client_id}&response_type=code"
        f"&redirect_uri={redirect_uri}&approval_prompt=force&scope={SCOPE}"
    )


def _token_request(data: dict) -> dict:
    r = requests.post(TOKEN_URL, data=data, timeout=30)
    r.raise_for_status()
    t = r.json()
    return {"access_token": t["access_token"], "refresh_token": t["refresh_token"], "expires_at": t["expires_at"]}


def exchange_code(client_id: str, client_secret: str, code: str) -> dict:
    return _token_request(
        {"client_id": client_id, "client_secret": client_secret, "code": code, "grant_type": "authorization_code"}
    )


def ensure_fresh(tok: dict) -> dict:
    """Refresh in place if the access token expires within 10 minutes. Strava may rotate the refresh token."""
    if tok.get("expires_at", 0) - time.time() > 600:
        return tok
    tok.update(
        _token_request(
            {
                "client_id": tok["client_id"],
                "client_secret": tok["client_secret"],
                "refresh_token": tok["refresh_token"],
                "grant_type": "refresh_token",
            }
        )
    )
    return tok


def fetch_activities(tok: dict, since: datetime) -> list[dict]:
    headers = {"Authorization": f"Bearer {tok['access_token']}"}
    out, page = [], 1
    while True:
        r = requests.get(
            f"{API}/athlete/activities",
            headers=headers,
            params={"after": int(since.timestamp()), "per_page": 100, "page": page},
            timeout=30,
        )
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        out.extend(normalize(a) for a in batch)
        page += 1
        if page > 20:
            break
    return out


def normalize(a: dict) -> dict:
    start = datetime.strptime(a["start_date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return {
        "id": f"strava:{a['id']}",
        "source": "strava",
        "date": a.get("start_date_local", a["start_date"])[:10],
        "start_utc": start.isoformat(),
        "sport": a.get("sport_type") or a.get("type") or "Workout",
        "name": a.get("name", ""),
        "duration_s": a.get("moving_time") or a.get("elapsed_time") or 0,
        "distance_m": a.get("distance") or 0,
        "avg_hr": a.get("average_heartrate"),
        "max_hr": a.get("max_heartrate"),
        "device_load": None,
    }
