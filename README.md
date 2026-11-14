# Swole & Sleepy

*How fit, how tired, and what to do about it today.*

A personal health and performance dashboard. It answers one question each morning:
**am I eating, exercising and recovering in ways that are improving my health?**

```
GitHub Action (4× a day) ──► Garmin Personal API · Withings · (Strava, optional)
        │                     MyFitnessPal ◄── CSV import now, API once access is granted
        ├─ state/tokens.enc.json   Withings/Strava OAuth tokens (encrypted, never served)
        └─ site/data.enc.json      raw history + signals        (encrypted, decrypted in your browser)
                                     ▲
                       Vercel serves site/ ── you unlock with your passphrase
```

Nothing is stored in plain text. Everything committed is AES-256-GCM encrypted, with the key
derived from one passphrase (PBKDF2-SHA256, 600k iterations). The page decrypts it in the browser.
There's no server and no database to run.

## Sources

Each platform owns its specialty, so nothing is counted twice:

| Source | Owns | How |
|---|---|---|
| Garmin | Workouts, training load, HRV, resting HR, sleep, steps, calorie burn, watch-wear times | Official Personal API key |
| Withings | Weight, body fat, fat and lean mass | OAuth |
| MyFitnessPal | Calories, protein, carbs, fat | CSV import today; API pending |
| Strava | Only workouts not recorded on the Garmin (optional) | OAuth |

Each provider is one module in `swole/` that returns the same normalized shape, so you can add,
swap or drop one without touching the dashboard.

## What it calculates

All the thresholds live in `config.json`. The formulas live in `swole/metrics.py`.

**Morning readiness (0–100)**
- HRV, resting HR and sleep are each compared with your own previous 60 days. HRV is compared on a log scale.
- Each signal scores 70 at baseline and moves 25 points per standard deviation. They're weighted 40/25/35.
- Two or more warnings force the day to "Recover". The result is a one-line call for the day.

**Sleep, even with the watch off**
- On nights you take the watch off, you're counted as asleep 45 minutes after the last heart-rate reading.
- You're counted as awake 5 minutes before the first reading the next morning.
- Nights Garmin tracked itself use Garmin's figure. HRV only exists on nights the watch is worn; other nights score on resting HR and sleep.

**Training**
- Every session gets a load score.
  - Runs and rides use Banister TRIMP from heart rate. Max HR is estimated from age (169) unless you set yours.
  - Swims and lifts use Garmin's own load, because wrist HR is unreliable in water and barely rises when lifting.
- Acute:chronic ratio is 7-day over 28-day load. Sweet spot 0.8–1.3; above 1.5 is a spike.
- Monotony is tracked, and so are weekly session targets (swim 2, run 3, lift 2).
- Each day is typed **swim / run / long run (75+ min) / lift / cycle / rest**. One day can have several types.

**Nutrition**
- Base targets are 2,350 kcal, 150 g protein, 270 g carbs and 75 g fat.
- Calories and carbs flex by day type: rest −250 kcal / −60 g carbs; long run +400 kcal / +100 g. Protein stays fixed.
- You also get 7-day averages and protein adherence.
- **Estimated** calorie balance is MFP intake minus Garmin's total burn, never MFP's own exercise calories.
  It's checked against what the scale trend actually shows.

**Body composition**
- One Withings reading per day, the earliest.
- You get a 7-day moving average and a smoothed trend (EWMA), with the rate taken as the trend's 28-day slope.
- Fat and lean mass trends are tracked.
- The goal is **17.5% body fat**. Its target weight is what you'd weigh at that body fat if lean mass held.
- Losing more than 1% a week, or losing lean mass while losing weight, is flagged.

## Setup

See **[SETUP.md](SETUP.md)** for the step-by-step checklist (about 25 minutes, on a computer).

## Importing MyFitnessPal

Until API access comes through:

1. On the MyFitnessPal website, export your food diary as CSV ("Nutrition Summary").
2. Then run:
   ```bash
   cd ~/swole-and-sleepy && source .venv/bin/activate && git pull
   export SWOLE_PASSPHRASE='…'
   python -m swole.nutrition ~/Downloads/Nutrition-Summary.csv
   git add site && git commit -m "Import food log" && git push
   ```

The importer sums meals per day. It matches column headers loosely, so other exports with
`Date`, `Calories`, `Protein (g)`, `Carbohydrates (g)` and `Fat (g)` columns work too.

## Day to day

- The job runs four times a day (about 7am, noon, 5pm and 9pm Eastern). Edit the cron lines in `.github/workflows/daily.yml` to change that.
- The chips at the top of the page show when each source last synced.
- If a source fails, everything else still saves, the run is marked failed, and GitHub emails you.
- **Probe Garmin:** Actions → Daily swole sync → Run workflow → tick *probe*.
  It prints the field names Garmin's endpoints return (never values). Use it if a Garmin number shows as "–".
- `python -m swole.demo` regenerates the sample data. Run the tests with `pip install pytest && pytest`.

## Caveats

- Garmin's Personal API is new (October 2026) and read-only. The parsers find fields by name and tolerate
  nesting changes, but the first real run should be checked with the probe.
- Withings and Strava rotate refresh tokens, which is why the job commits `state/tokens.enc.json` back.
- `site/data.enc.json` is publicly downloadable from your Vercel URL. It's encrypted, but its safety rests on
  your passphrase, so keep it long and random.
- Calorie burn, calorie balance and scale body fat are estimates. Correlations are not causes.
  None of this is medical advice.
