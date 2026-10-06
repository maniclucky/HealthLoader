# HealthLoader — full setup guide

A Python command-line tool (`fitthing`) that signs in with your Google account
and downloads your logged health data from the [Google Health
API](https://developers.google.com/health) to your own computer, as JSON
files.

It exports steps, heart rate, sleep, weight, exercise sessions, and 30+ other
data types across six categories (activity & fitness, health metrics, sleep,
nutrition, ECG, irregular rhythm notifications), backfilling the last 30 days
of history on first run (up to a year with `--days`) and staying up to date
on every rerun after that.

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
fitthing export      # first run: backfills last 30 days; every rerun: catches up since last time
```

Useful flags:

- `--types steps,weight,sleep` — export only specific data types (default: all granted types)
- `--since YYYY-MM-DD` / `--until YYYY-MM-DD` — override the date range
- `--full` — ignore saved sync state and re-backfill from scratch
- `--days N` — on a fresh backfill (no prior state, or with `--full`), backfill N days instead of the default 30 (max 365)

Rerun `fitthing export` any time — it's safe and idempotent. It re-checks
the last 3 days of any previous run (devices sometimes sync late) and picks
up new data since, without re-downloading or duplicating anything already
on disk. Today's date is always excluded — a day still in progress isn't a
complete reading — and gets picked up automatically on a later run once
it's actually over.

## 5. Cronometer: daily calories & macros (optional)

A second, independent data source: daily total calories/protein/carbs/fat
from [cronometer.com](https://cronometer.com), if you log food there. This
rides Cronometer's own web app endpoints (there's no public API for
personal accounts), automated with your normal cronometer.com username and
password — no separate app registration, unlike the Google setup above.

```bash
fitthing cronometer-auth      # prompts for your Cronometer username/password
fitthing cronometer-export    # first run: backfills last 30 days; every rerun: catches up
```

`cronometer-auth` signs in once and saves both the session
(`secrets/cronometer_session.json`) and, so you're not asked again, your
credentials (`secrets/cronometer.json`, `chmod 600`) — unless you set
`CRONOMETER_USERNAME`/`CRONOMETER_PASSWORD` environment variables instead,
which is the no-storage-on-disk option. `cronometer-export` reuses the
cached session and only re-authenticates if it's expired.

Same `--since`, `--until`, `--full`, and `--days` flags as `export` above,
and the same "today is always excluded" rule.

**Cronometer's export is capped at ~10/day, personal use only.** Unlike the
Google path, this is never windowed — one request covers the whole
backfill range or the whole incremental catch-up, so a normal day costs 1
of the 10.

**If you have 2FA enabled on Cronometer, automated sign-in won't work.**
Export the Daily Summary CSV yourself from Cronometer's web UI (Account
Data → Export) and run:

```bash
fitthing cronometer-import path/to/exported.csv
```

This is the same parser and merge logic `cronometer-export` uses, so the
result is identical either way. It's also the break-glass path if
Cronometer redeploys their frontend and automated sign-in starts failing
outright (see below).

**What's stored:** `data/nutrition/daily.json`, one entry per date —
`calories_kcal`, `protein_g`, `carbs_g`, `fat_g`, plus a `nutrients` map of
every other numeric column in the export (vitamins, minerals, fiber, ...) —
and the untouched raw CSV at
`data/raw/cronometer-daily-summary/{start}_{end}.csv`. This is daily totals
only: no per-food detail, no Cronometer biometrics/exercise (weight and activity already come from
Google Health above). Cronometer's days are your local calendar days,
already summed by Cronometer — not the UTC windows Google's `nutrition-log`
uses, so the two aren't merged or reconciled; if you log in both places,
treat this file as the more accurate one for "what did I eat."

**If Cronometer redeploys their web app, automated sign-in/export can start
failing** — this integration rides undocumented endpoints, not a stable
API. The error message names the `gwt_permutation` value it was using and
points here: `agent-notes/2026-09-17-cronometer-endpoints.md` documents how
to re-capture the current values from your browser's DevTools and update
`config.toml` (`gwt_permutation`) and `src/fitthing/cronometer/auth.py`
(the request body templates). Until you do, `cronometer-import` still
works from a manually exported CSV.

## 6. Pull everything at once

```bash
fitthing sync          # Cronometer, then Google Health, then rebuild daily.csv
fitthing sync --full   # re-backfill both sources from scratch
fitthing sync --days 90
```

`sync` runs the same pulls as `cronometer-export` and `export`, in that
order, so the CSV rebuilt at the end includes the fresh nutrition. If one
source fails (expired sign-in, network, API errors) the other still runs and
the CSV is still rebuilt from whatever is on disk; the exit code is 1 if
either source failed. Sign in to both first (`fitthing auth`,
`fitthing cronometer-auth`).

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
is lost — you can post-process any type not covered below into CSV or
another format yourself.

**Not exported:** `food` and `food-measurement-unit` — these turned out to
be Google's global reference catalogs (a food/nutrition database), not your
personal logs. `nutrition-log` (what you actually logged) is exported
normally.

### Day-level CSV

Every `fitthing export` or `fitthing sync` run also rebuilds
`data/health/daily.csv`: columns `Energy (kcal)`, `Protein (g)`,
`Carbs (g)`, `Fat (g)`, `Weight (lbs)`, `Body Fat (%)`, `Steps`,
`Energy Burned (kcal)`, `Avg Heart Rate (bpm)`,
`Resting Heart Rate (bpm)`, `SpO2 (%)`, `Sleep (hrs)`, `Exercise (min)`,
then every other Cronometer nutrient (alphabetical), one row per calendar day,
covering the trailing `csv_days` days (default 30, in `config.toml`) —
**every** day in that window gets a row, even with no data at all, so gaps
are visible rather than silently missing.

Sources:
- Nutrition columns come from Cronometer's `data/nutrition/daily.json`.
  Days merged before nutrients were stored only have the four core macros;
  `fitthing cronometer-export --full` (or `cronometer-import` of the raw
  CSVs) fills in the rest.
- The rest come from rescanning whatever raw Google JSON is currently on
  disk (not just what this run fetched). Weight, body fat, heart rate,
  resting heart rate, and SpO2 average same-day values; energy burned and
  sleep are summed. Sleep is credited to the local day the session ended
  (the morning you woke up) and counts minutes asleep, naps included.
  Exercise is wall-clock minutes in any exercise session, with overlapping
  sessions (e.g. phone and treadmill logging the same walk) counted once.

Steps and energy burned are summed only from the source app(s) listed in
`csv_device_packages` in `config.toml` (default: `com.gdjztech.ringconn`,
a single wearable) — otherwise a phone and a ring both reporting the same
walk would double it. Set `csv_device_packages = []` to sum across every
source instead.

**Treadmill steps:** while a session from an app in
`csv_treadmill_packages` (default `com.urevo.app`) is running, the
treadmill's step count replaces the wearable's for that stretch. A ring
misses steps when your hand is still, e.g. working at a walking desk. The
ring logs steps in 30-minute buckets, so a bucket that's partly inside a
session keeps only the share outside it. Overlapping treadmill records
(the same session uploaded twice) count once. Energy burned is still
ring-only; the treadmill doesn't report it.

**Gap-filling**, applied after everything above:
- A missing **weight/body fat** day is linearly interpolated between the
  nearest known reading before and after it. A leading or trailing gap (no
  reading on one side within the window) is left blank rather than
  extrapolated.
- A missing **nutrition** day (nothing at all logged to Cronometer) is
  filled from `target_settings_path` in `config.toml` (default
  `targetSettings.json`, gitignored — a small JSON file of your own daily
  targets: `{"calories": 2200, "protein": 150, "carbs": 250, "fat": 70}`).
  A day that *was* logged is never overridden, even partially. No file, or
  a key it doesn't have, just leaves that cell blank.
- Steps, energy burned, the other Google columns, and the extra nutrients are never filled either way — there's no sane
  default for "how many steps did I take."

This is a full rebuild every run, not an incremental merge — necessary for
the gap-filling above to be correct, and because incremental Google syncs
re-fetch the last few days on every run (see `export`'s overlap behavior
above): the freshest fetch for a given day always wins, so re-running never
double-counts a day even if two raw window files on disk cover it.

## Repository layout

- `agent-notes/` — shared reference space for AI agents (and humans). See `agent-notes/README.md`.
- `src/fitthing/` — the `fitthing` package; the Google Health path (`client.py`, `datatypes.py`,
  `windows.py`, `export.py`, `aggregate.py`) and `src/fitthing/cronometer/` (`auth.py`, `client.py`,
  `parse.py`, `export.py`) are independent data sources that don't share code, since they have
  almost nothing in common beyond "download health data on a schedule."
- `tests/` — pytest suite (no network calls).
- `config.example.toml` — copy to `config.toml` (gitignored) and edit.
- `secrets/`, `data/` — gitignored; created locally, never committed.

## Development

```bash
pytest
```
