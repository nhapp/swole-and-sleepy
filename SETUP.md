# Swole & Sleepy: setup checklist

Do this on a computer, in one sitting of about 25 minutes. Commands are for a Mac Terminal.
Repo: https://github.com/nhapp/swole-and-sleepy

---

## 1. Passphrase → GitHub (5 min)

1. Make a passphrase in Terminal:
   ```
   python3 -c "import secrets;print(secrets.token_urlsafe(24))"
   ```
   **Save it in your password manager now.** Without it, the data can't be decrypted.
2. Go to https://github.com/nhapp/swole-and-sleepy/settings/secrets/actions, click **New repository secret**,
   name it `SWOLE_PASSPHRASE`, paste the passphrase, and save.

## 2. Garmin API key → GitHub (3 min)

1. Sign in to Garmin Connect on the web and open https://connect.garmin.com/app/settings/personalApiToken.
2. Accept the terms and create a key. Copy it.
3. Back on the GitHub secrets page, add another secret named `GARMIN_PERSONAL_API_KEY` and paste the key.

That's all Garmin needs. There's no password or MFA, and nothing to renew.

## 3. Put the dashboard online with Vercel (5 min)

1. Go to https://vercel.com/new and import **swole-and-sleepy**. If it isn't listed, use "Adjust GitHub App Permissions".
2. Leave every setting as is and click **Deploy**.
3. Write down the URL, for example `https://swole-and-sleepy.vercel.app`.

## 4. Connect Withings (8 min)

You need Python **3.12 or newer** for this step. Check with `python3 --version`; if it's older, install the latest from https://www.python.org/downloads/.

```
cd ~
git clone https://github.com/nhapp/swole-and-sleepy
cd swole-and-sleepy
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export SWOLE_PASSPHRASE='paste-your-passphrase-here'
```

1. Go to https://developer.withings.com/dashboard/ and create an application. Choose **Public API integration**.
   For the **callback URL**, enter your Vercel URL plus `/oauth-callback.html`,
   for example `https://swole-and-sleepy.vercel.app/oauth-callback.html`.
2. Run `python -m swole.setup_auth withings` and paste the callback URL, Client ID and Client Secret.
3. Open the link it prints and click **Allow**.
4. The page you land on shows a URL in a box. Paste it into Terminal **right away**; the code expires in about 30 seconds.

## 5. Personalize and push (3 min)

1. Open `config.json` in a text editor:
   - `"timezone"`: for example `"America/New_York"`
   - `"max_hr"`: your real max heart rate if Garmin shows one. Otherwise leave it `null` and it's estimated from age.
   - Check the `"nutrition"` targets and `"weekly_targets"`. They're preset from your plan.
2. Then run:
   ```
   git add state config.json && git commit -m "Connect Withings and personalize" && git push
   ```

## 6. First run (3 min)

1. Go to https://github.com/nhapp/swole-and-sleepy/actions, click **Daily swole sync**, then **Run workflow**.
   Tick **probe** the first time. It checks that Garmin answers and prints only field names.
   Then run it again without probe.
2. Open your Vercel URL, enter the passphrase, and tick "Remember on this device".
   Do the same on your iPhone, then Share → Add to Home Screen.

## Optional

- **MyFitnessPal (until API access arrives):** export the CSV from the MFP website, then
  `python -m swole.nutrition ~/Downloads/<file>.csv`, then `git add site && git commit -m "Food log" && git push`.
- **Strava:** only needed for workouts not recorded on your Garmin. Run `python -m swole.setup_auth strava`
  (set the app's Authorization Callback Domain to `localhost`).

## If something goes wrong

- **Red X on an Actions run:** open it. The "Pull data and recompute" step names the source that failed.
  - **Garmin 401:** create a new key and update the `GARMIN_PERSONAL_API_KEY` secret.
  - **Withings auth error:** redo step 4, then push.
- **"That passphrase didn't decrypt your data":** the passphrase on the page doesn't match the GitHub secret.
- **Readiness says "Calibrating":** normal for the first two weeks.
