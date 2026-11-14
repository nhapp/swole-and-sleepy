"""One-time, local OAuth authorization for Withings (and optionally Strava). Run on your own computer.

Garmin needs no step here: its personal API key goes straight into the
GARMIN_PERSONAL_API_KEY GitHub secret.

    export SWOLE_PASSPHRASE='a long passphrase'
    python -m swole.setup_auth strava
    python -m swole.setup_auth withings

Each command stores the resulting tokens in state/tokens.enc.json (encrypted).
Commit and push that file afterwards.
"""

import getpass
import sys
from urllib.parse import parse_qs, urlparse

from . import crypto, strava, withings
from .run import TOKENS_PATH, passphrase

REDIRECT_URI = "http://localhost/swole"


def _code_from_pasted_url(redirect_uri: str) -> str:
    print("\nAfter approving, your browser lands on")
    print(f"  {redirect_uri}?...code=...   (it's fine if that page doesn't load)")
    url = input("Paste the full URL from the address bar: ").strip()
    qs = parse_qs(urlparse(url).query)
    if "code" not in qs:
        sys.exit(f"No ?code= in that URL ({qs.get('error', ['unknown error'])[0]})")
    return qs["code"][0]


def setup_strava(tokens: dict):
    print("Create an app at https://www.strava.com/settings/api")
    print("  Authorization Callback Domain: localhost")
    cid = input("Strava Client ID: ").strip()
    secret = getpass.getpass("Strava Client Secret: ").strip()
    print("\nOpen this URL and approve access:\n  " + strava.authorize_url(cid, REDIRECT_URI))
    tok = strava.exchange_code(cid, secret, _code_from_pasted_url(REDIRECT_URI))
    tokens["strava"] = {"client_id": cid, "client_secret": secret, **tok}
    print("Strava connected.")


def setup_withings(tokens: dict):
    print("Create an app at https://developer.withings.com/dashboard/ (Public API integration)")
    print("Withings rejects localhost callbacks, so use your deployed dashboard, e.g.")
    print("  https://your-dashboard.vercel.app/oauth-callback.html")
    redirect = input("Callback URL you registered: ").strip()
    cid = input("Withings Client ID: ").strip()
    secret = getpass.getpass("Withings Client Secret: ").strip()
    print("\nOpen this URL and approve access:\n  " + withings.authorize_url(cid, redirect))
    # Withings authorization codes expire after 30 seconds — paste promptly.
    tok = withings.exchange_code(cid, secret, _code_from_pasted_url(redirect), redirect)
    tokens["withings"] = {"client_id": cid, "client_secret": secret, **tok}
    print("Withings connected.")


def main():
    which = sys.argv[1:] or ["withings"]
    pw = passphrase()
    tokens = crypto.read_file(TOKENS_PATH, pw, default={})
    for name in which:
        print(f"\n=== {name} ===")
        {"strava": setup_strava, "withings": setup_withings}[name](tokens)
        crypto.write_file(TOKENS_PATH, tokens, pw)
    print(f"\nSaved {TOKENS_PATH}. Commit and push it, then run the GitHub Action once.")


if __name__ == "__main__":
    main()
