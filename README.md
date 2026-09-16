# FitThing

A Python command-line tool (`fitthing`) that signs in with your Google account
and downloads your logged health data from the [Google Health
API](https://developers.google.com/health) to your own computer, as JSON
files.

It exports steps, heart rate, sleep, weight, exercise sessions, and 30+ other
data types across six categories (activity & fitness, health metrics, sleep,
nutrition, ECG, irregular rhythm notifications), backfilling one year of
history on first run and staying up to date on every rerun after that.

## 1. Prerequisites: Google Cloud setup

The Google Health API requires you to register your own app in Google Cloud
— there's no shared key. This is a one-time setup.

1. Create or select a project at [console.cloud.google.com](https://console.cloud.google.com).
2. Enable the **Google Health API**: [console.developers.google.com/apis/library/health.googleapis.com](https://console.developers.google.com/apis/library/health.googleapis.com).
3. Set up the OAuth consent screen (Google now calls this **Google Auth
   Platform**): **APIs & Services → OAuth consent screen**.
   - **User Type: External.**
   - Fill in the required fields (app name, your email for support/developer
     contact). Leave publishing status as **Testing** — don't click Publish.
   - On the **Audience** tab, add your own Google account under **Test
     users**. Only test users can sign in while the app is unverified.
4. On the **Data Access** tab, add these six scopes:
   - `https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly`
   - `https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly`
   - `https://www.googleapis.com/auth/googlehealth.sleep.readonly`
   - `https://www.googleapis.com/auth/googlehealth.nutrition.readonly`
   - `https://www.googleapis.com/auth/googlehealth.ecg.readonly`
   - `https://www.googleapis.com/auth/googlehealth.irn.readonly`
5. On the **Credentials** tab, create an OAuth client of type **Desktop
   app**. Download the JSON and save it as `secrets/client_secret.json` in
   this repo (the `secrets/` folder is gitignored — never commit this file).

**If your health data comes from the Google Fit app** (aggregating other
fitness apps) rather than a Fitbit device, you also need the separate
**Google Health app** installed on your phone, with its Health Connect
sync completed — this is what actually links your account so the API has
data to return. See `agent-notes/2026-09-16-account-not-linked-finding.md`
for the full story if you hit `ACCOUNT_NOT_LINKED`.

## 2. Install

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate       # every new terminal needs this line
pip install -e '.[dev]'
cp config.example.toml config.toml
```

Edit `config.toml` if you want to change where data is written or which
types are exported (default: `"all"`).

## 3. Sign in

```bash
fitthing auth
```

Opens a browser to sign in and consent to the six scopes above. Saves your
token to `secrets/token.json` (gitignored).

Verify it worked:

```bash
fitthing whoami
```

**Testing-mode sign-ins expire every 7 days.** Every command checks this
automatically: if your sign-in has expired, it explains why and asks
**"Sign in now? A browser window will open. [Y/n]"** before continuing — no
need to remember to run `fitthing auth` yourself. Running non-interactively
(no terminal attached, or `--no-prompt`) skips the prompt and exits with
code 2 instead, printing `Sign-in required. Run: fitthing auth`.

## 4. Export your data

```bash
fitthing types      # list every exportable data type and whether it's granted
fitthing export      # first run: backfills 1 year; every rerun: catches up since last time
```

Useful flags:

- `--types steps,weight,sleep` — export only specific data types (default: all granted types)
- `--since YYYY-MM-DD` / `--until YYYY-MM-DD` — override the date range
- `--full` — ignore saved sync state and re-backfill from scratch

Rerun `fitthing export` any time — it's safe and idempotent. It re-checks
the last 3 days of any previous run (devices sometimes sync late) and picks
up new data since, without re-downloading or duplicating anything already
on disk.

## Output format

Each 14-day query window is written to its own file:

```
data/raw/{data_type}/{window_start}_{window_end}.json
```

e.g. `data/raw/steps/2026-01-01_2026-01-15.json`, containing:

```json
{
  "data_type": "steps",
  "window_start": "2026-01-01",
  "window_end": "2026-01-15",
  "fetched_at": "2026-01-15T03:00:00+00:00",
  "filter": "steps.interval.start_time >= \"2026-01-01T00:00:00Z\" AND ...",
  "count": 1344,
  "dataPoints": [ /* raw API objects, unmodified */ ]
}
```

`data/state.json` tracks how far each data type has synced. Both `data/`
and `secrets/` are gitignored — nothing personal ever gets committed.

Data is saved as-is from the API (no flattening or reformatting), so nothing
is lost — you can always post-process it into CSV or another format later.

**Not exported:** `food` and `food-measurement-unit` — these turned out to
be Google's global reference catalogs (a food/nutrition database), not your
personal logs. `nutrition-log` (what you actually logged) is exported
normally.

## Repository layout

- `agent-notes/` — shared reference space for AI agents (and humans). See `agent-notes/README.md`.
- `src/fitthing/` — the `fitthing` package.
- `tests/` — pytest suite (no network calls).
- `config.example.toml` — copy to `config.toml` (gitignored) and edit.
- `secrets/`, `data/` — gitignored; created locally, never committed.

## Development

```bash
pytest
```
