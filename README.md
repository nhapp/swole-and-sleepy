# Swole & Sleepy

*How fit, how tired, and what to do about it today.*

Each day a GitHub Action pulls your Garmin Connect, Strava and Withings data. It turns that into
three signals — **recovery**, **training load balance** and **body composition trend** — and
gives a one-line call for the day. A static page (`site/index.html`) shows it all.

```
GitHub Action (daily) ──► Garmin / Strava / Withings
        │
        ├─ state/tokens.enc.json   rotated OAuth tokens   (encrypted, never served)
        └─ site/data.enc.json      raw history + signals  (encrypted, decrypted in your browser)
                                     ▲
                       Vercel serves site/ ── you unlock with your passphrase
```

Nothing is stored in plain text. Everything committed is AES-256-GCM encrypted, with the key
derived from one passphrase (PBKDF2-SHA256, 600k iterations). The page decrypts it in the browser.

## What it calculates

All the thresholds live in `config.json`. The formulas live in `swole/metrics.py`.

**Recovery**
- HRV, resting HR and sleep are each compared with your own previous 60 days. HRV is compared on a log scale.
- Each signal scores 70 at baseline and moves 25 points per standard deviation.
- They combine into a 0–100 readiness score using the weights in `config.json`. Two or more warnings force the day to "Recover".
- **Sleep estimate from watch-off time.** On nights you take the watch off, you're counted as asleep
  45 minutes after the last heart-rate reading. You're counted as awake 5 minutes before the first reading
  the next morning. The night is the longest no-wear gap of at least 3 hours between 18:00 and 14:00.
  Shorter gaps, like a shower, are ignored. Nights Garmin tracked itself use Garmin's sleep instead
  (`"sleep_source": "auto"`; set `"proxy"` or `"garmin"` to force one).
- HRV only exists on nights the watch is worn. On other nights readiness runs on resting HR and sleep.
- **Steps** come from Garmin. A 7-day average under 60% of `activity.steps_goal` raises a note.

**Training load**
- Every Garmin and Strava activity is counted once. When both have it, the Garmin copy wins.
- Each session is scored with Banister TRIMP from average HR.
- The acute:chronic ratio is the 7-day over the 28-day average load. Sweet spot 0.8–1.3; above 1.5 is a spike.
- Monotony is also tracked.

**Body composition**
- One Withings reading per day, the earliest.
- The trend is an exponential moving average (α 0.1). The rate is the trend's 28-day slope.
- Fat and lean mass changes are flagged when you lose more than 1% a week, or lose lean mass while losing weight.

## Setup (about 30 minutes, once)

### 1. Repo, passphrase, hosting

1. Push this folder to a **private** GitHub repo.
2. Make a long random passphrase, for example from `python -c "import secrets;print(secrets.token_urlsafe(24))"`.
   Add it as a repo secret named `SWOLE_PASSPHRASE` (Settings → Secrets and variables → Actions).
3. Import the repo in Vercel. `vercel.json` already serves only `site/`. Note the URL, for example
   `https://my-health.vercel.app`.

### 2. Authorize the three sources (on your own computer)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export SWOLE_PASSPHRASE='the same passphrase'
```

**Garmin.** Run `python -m swole.setup_auth garmin`. It asks for your email, password and MFA code.
The password is not stored, only the resulting token. Do this locally: Garmin often blocks logins
from cloud IPs, but token refreshes from GitHub work.

**Strava**
1. Go to <https://www.strava.com/settings/api> and create an app. Set the *Authorization Callback Domain* to `localhost`.
2. Run `python -m swole.setup_auth strava`. Paste the Client ID and Secret, open the printed URL, and approve.
3. Your browser lands on a `localhost` page that doesn't load. That's expected: copy the whole URL from the address bar and paste it back.

**Withings**
1. Go to <https://developer.withings.com/dashboard/> and create a *Public API integration* app.
   Withings rejects `localhost`, so set the callback URL to `https://<your-vercel-url>/oauth-callback.html`.
2. Run `python -m swole.setup_auth withings` and follow the same steps.
   The callback page shows the URL ready to copy. Withings codes expire after about 30 seconds, so paste it promptly.

Then commit `state/tokens.enc.json`, push, and run the workflow once by hand (Actions → Daily swole sync → Run workflow).
The first run backfills 180 days, or 90 days of watch-wear data.

### 3. Tune `config.json`

Set `timezone`, `units` (`imperial` / `metric`), `profile.max_hr` (used for TRIMP; your real max beats 220−age),
`profile.sex`, `activity.steps_goal`, and optionally `body.goal_weight_kg`.
Changes take effect on the next run.

## Day to day

- The job runs at 11:17 UTC. Change the cron in `.github/workflows/daily.yml` if you want it at another time.
- The source chips at the top of the page show when each source last synced.
- If a source fails, the workflow still saves everything else, marks the run failed, and GitHub emails you.
- If one source keeps failing with an auth error, re-run `setup_auth <source>` for it and push.
- `python -m swole.demo` regenerates `site/demo.json`. The page's "View with sample data" uses it.
- Run the tests with `pip install pytest && pytest`.

## Caveats

- Garmin has no personal API. `garminconnect` is unofficial and can break when Garmin changes its login.
  If it does, `pip install -U garminconnect` and re-run `setup_auth garmin`.
- All three providers rotate refresh tokens, which is why the job commits `state/tokens.enc.json` back after
  every run. Don't run it from two places at once.
- `site/data.enc.json` is publicly downloadable from your Vercel URL. It's encrypted, but its safety rests
  entirely on your passphrase, so keep it long and random.
- These are trend heuristics, not medical advice.
