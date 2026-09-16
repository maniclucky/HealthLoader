# Plan: Google Health API → local export script

**Summary:** Build a Python CLI (`fitthing`) that authenticates with Google OAuth, downloads the user's logged health data from the Google Health API v4, and saves it on disk as raw JSON with incremental re-sync.
**Date:** 2026-09-16 · **Author:** Claude (Opus 5), planning · **Status:** DRAFT, waiting for user review
**Executor:** a follow-up agent. Do the phases in order. Do not skip acceptance checks.

---

## 0. Rules for the executing agent

1. **Stop and ask the user** if the live API behaves differently from what this plan describes (field names, filters, errors). Do not guess. Record what you found in a new note in `agent-notes/`.
2. **Never commit secrets or health data.** `secrets/` and `data/` must be in `.gitignore` *before* any credentials file exists.
3. Request only **read-only** scopes (`*.readonly`). Never call write, patch, or delete endpoints.
4. Commit at the end of each phase with a clear message. Run the tests before each commit.
5. Tasks marked **[USER]** need a human in a browser or the Google Cloud Console. Give the user exact instructions and wait for them.
6. At the end, add a short handoff note in `agent-notes/` and update `agent-notes/INDEX.md`.

---

## 1. Background (verified 2026-09-16)

- **API:** Google Health API v4. Base URL: `https://health.googleapis.com/v4/`. It replaces the Fitbit Web API, which shuts down in Sept 2026. The Google Fit REST API ends in late 2026, so **do not use Google Fit**.
- **Auth:** standard Google OAuth 2.0. Every Health API scope is *Restricted*, so an unverified app stays in **Testing** mode (the user must add themselves as a test user). **In Testing mode, refresh tokens expire after 7 days**, so the script has to handle re-authentication cleanly.
- **Endpoints used (read only):**
  - `GET /v4/users/me/identity`: smoke test
  - `GET /v4/users/me/dataTypes/{dataType}/dataPoints`: `list`, the main export call
  - `GET /v4/users/me/pairedDevices`: optional metadata
- **`list` parameters:** `pageSize` (max 10,000; **max 25 for `sleep` and `exercise`**), `pageToken`, and `filter` (AIP-160, operators `>=`, `<`, `AND`). The response is `{ "dataPoints": [...], "nextPageToken": "..." }`. An empty or missing token means the last page. Results come back newest first.
- **The filter field depends on the record kind.** In URLs, data type IDs are kebab-case (`body-fat`). In filters they are **snake_case** (`body_fat`).

  | Record kind | Filter field | Value format |
  |---|---|---|
  | Interval (steps, distance, …) | `{t}.interval.start_time` | RFC-3339 UTC, e.g. `"2026-01-01T00:00:00Z"` |
  | Sample (weight, heart-rate, …) | `{t}.sample_time.physical_time` | RFC-3339 UTC |
  | Daily summary (`daily-*`) | `{t}.date` | `"YYYY-MM-DD"` |
  | Session: exercise, hydration-log, nutrition-log, irregular-rhythm-notification | `{t}.interval.civil_start_time` | ISO-8601 civil, e.g. `"2026-01-01T00:00:00"` |
  | Session: `sleep` | `sleep.interval.end_time` | RFC-3339 UTC |
  | Session: `electrocardiogram` | `electrocardiogram.interval.start_time` | RFC-3339 UTC |
  | Food (`food`, `food-measurement-unit`) | **unknown, verify in Phase 2** | — |

- **Query range:** the docs cap *rollups* at 90 days (14 days for `heart-rate`, `active-minutes`, `total-calories`, `calories-in-heart-rate-zone`). No limit is documented for `list`, but **use 14-day windows for every type anyway**. That's safe and keeps each response small.
- **Rate limits:** not documented. Handle 429 and 5xx with backoff.
- **Reference implementation:** `github.com/Google-Health-API/google-health-cli` (Go, Apache-2.0). It uses a **Desktop** OAuth client with PKCE. Read it if behavior is unclear. Do not copy its code wholesale.
- Docs: https://developers.google.com/health · https://developers.google.com/health/data-types · https://developers.google.com/health/reference/rest/v4/users.dataTypes.dataPoints/list

### Readable data types (initial registry)
Scope prefix: `https://www.googleapis.com/auth/googlehealth.`

| Scope suffix | Data type IDs (kind) |
|---|---|
| `activity_and_fitness.readonly` | steps, distance, altitude, active-energy-burned, active-minutes, active-zone-minutes, activity-level, sedentary-period, swim-lengths-data, time-in-heart-rate-zone (Interval); vo2-max, run-vo2-max (Sample); daily-vo2-max (Daily); exercise (Session) |
| `health_metrics_and_measurements.readonly` | heart-rate, heart-rate-variability, weight, height, body-fat, blood-glucose, oxygen-saturation, core-body-temperature, respiratory-rate-sleep-summary (Sample); daily-heart-rate-variability, daily-heart-rate-zones, daily-oxygen-saturation, daily-respiratory-rate, daily-resting-heart-rate, daily-sleep-temperature-derivations (Daily) |
| `sleep.readonly` | sleep (Session) |
| `nutrition.readonly` | hydration-log, nutrition-log (Session); food, food-measurement-unit (Food) |
| `ecg.readonly` | electrocardiogram (Session) |
| `irn.readonly` | irregular-rhythm-notification (Session) |

Leave these out: `floors`, `total-calories`, `calories-in-heart-rate-zone` (the docs say `list` isn't supported), and every write-only type (menstrual-period, ovulation-test, moods, symptoms).

---

## 2. Target layout

```
FitThing/
├── pyproject.toml            # package metadata, deps, console script `fitthing`
├── README.md                 # setup + usage (update)
├── .gitignore                # add: secrets/, data/
├── config.example.toml       # copy to config.toml (gitignored)
├── secrets/                  # gitignored: client_secret.json, token.json
├── data/                     # gitignored: exported data
├── src/fitthing/
│   ├── __init__.py
│   ├── __main__.py           # `python -m fitthing`
│   ├── cli.py                # argparse subcommands
│   ├── config.py             # load config.toml + CLI overrides
│   ├── auth.py               # OAuth flow, token storage/refresh
│   ├── client.py             # HTTP session, retries, pagination
│   ├── datatypes.py          # registry: id, kind, filter field, scope, page size
│   ├── windows.py            # date-range chunking + filter string building
│   └── export.py             # orchestrates fetch → write → state
└── tests/                    # pytest, no network
```

**Dependencies:** Python ≥ 3.11 (for `tomllib`), `requests`, `google-auth`, `google-auth-oauthlib`. Dev: `pytest`, `responses` (HTTP mocking). Nothing else.

---

## 3. Phases

### Phase 1: Scaffolding
- Create the layout above, `pyproject.toml` (src layout, `[project.scripts] fitthing = "fitthing.cli:main"`), and a venv at `.venv/`.
- Add `secrets/`, `data/`, and `config.toml` to `.gitignore`.
- `config.example.toml` keys: `client_secret_path`, `token_path`, `output_dir`, `start_date` (default: 1 year ago), `data_types` (list, or `"all"`), `window_days = 14`.
- **Accept when:** `pip install -e .[dev]` works, `fitthing --help` prints the subcommands, and `pytest` runs (0 tests is fine).

### Phase 2: Google Cloud setup [USER] + auth
1. **[USER]** Give the user these steps, then wait:
   1. Create or select a Google Cloud project.
   2. Enable **Google Health API** (`health.googleapis.com`).
   3. Set up the OAuth consent screen: External, Testing. Add their own Google account under **Audience → Test users**.
   4. Under **Data Access**, add the read-only scopes they chose (see §4).
   5. Under **Credentials**, create an OAuth client of type **Desktop app**. Download the JSON to `secrets/client_secret.json`.
2. `auth.py`:
   - Use `google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file(...).run_local_server(port=0, access_type="offline", prompt="consent")`. This is a loopback redirect, and PKCE is handled by the library.
   - Save the credentials with `creds.to_json()` to `token_path`, file mode `0600`.
   - `get_credentials()`: load the token. If expired, refresh it. On `google.auth.exceptions.RefreshError`, **or** if the stored scopes don't cover the configured ones, print "Re-authorization required (Testing-mode tokens expire every 7 days). Run: fitthing auth" and exit with code 2. Don't open a browser on its own during `export`.
3. CLI `fitthing auth` runs the flow. `fitthing whoami` calls `GET /users/me/identity` and prints the result.
4. **Accept when [USER]:** `fitthing auth` opens a browser, consent works, and `fitthing whoami` prints an identity.
5. **Verify live and record findings:** for one type of each kind (steps, weight, daily-resting-heart-rate, exercise, sleep, food), do a 14-day `list`. Confirm that the filter fields in §1 work. Find the working filter for `food`, or confirm it has none. Write the results to `agent-notes/` and fix the registry if anything differs.

### Phase 3: API client
- `client.py`: `HealthClient(credentials)` wraps `google.auth.transport.requests.AuthorizedSession`, which refreshes on 401 automatically.
  - `list_data_points(data_type, filter, page_size)` is a generator that yields each data point across all pages.
  - Retries: on 429, 500, 502, 503, 504, use exponential backoff (1s, 2s, 4s, 8s, 16s, plus jitter), honor `Retry-After`, and allow 5 attempts max.
  - On 403 or 400, raise `ApiError(status, body)`. Don't retry.
  - Timeout 60s per request.
- `datatypes.py`: a frozen dataclass `DataType(id, kind, scope, filter_field, time_format, page_size)` and a `REGISTRY` dict built from the §1 tables. `snake = id.replace("-", "_")`. Page size is 25 for sleep and exercise, 10000 for everything else.
- `windows.py`: `iter_windows(start: date, end: date, days: int)` yields half-open `[a, b)` ranges. `build_filter(dt, a, b)` returns `f'{field} >= "{A}" AND {field} < "{B}"'`, with A and B formatted according to `time_format`.
- **Accept when:** unit tests cover window edges (partial last window, start == end), a filter string for each kind, pagination over 3 mocked pages, retry on 429 then success, and no retry on 403.

### Phase 4: Export + incremental sync
- **Output format (raw, lossless):** `data/raw/{data_type}/{window_start}_{window_end}.json` containing
  `{"data_type", "window_start", "window_end", "fetched_at", "filter", "count", "dataPoints": [...]}`.
  Write to `*.tmp`, then `os.replace` it into place so a crash never leaves a partial file.
- **State file:** `data/state.json` maps each type to `{"synced_through": "YYYY-MM-DD", "last_run": ...}`. Update it after each successful window, not only at the end.
- `fitthing export [--types a,b] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--full]`
  - Default range: from `state.synced_through - 3 days` (overlap, because devices sync late), or `config.start_date` if the type has no state, **through today inclusive** (end = tomorrow, exclusive). Re-fetched windows overwrite their files.
  - `--full` ignores state.
  - Only types whose scope was granted get exported. Log a warning for the rest.
  - A 403 on a type logs the error and moves to the next type. Other errors stop the run with exit code 1. State is saved up to the last good window.
  - Log progress to stderr: `steps 2026-01-01→2026-01-15: 1344 points`.
- `fitthing types` lists the registry with ✔/✘ for granted scopes.
- **Accept when:** a mocked end-to-end test writes the expected files and state, and a second run starts from the overlap. **[USER]** runs a live export for 30 days and checks that the files look right.

### Phase 5: Docs & wrap-up
- README: prerequisites, the Cloud setup steps (from Phase 2), install, `auth`, `export`, the 7-day re-auth note, and the output format.
- Add a handoff note in `agent-notes/` (what was verified live, known gaps). Update `INDEX.md`. Commit.

### Deferred (not in this plan unless the user asks)
Flattened CSV/Parquet exports per type, `dailyRollUp` summaries, `pairedDevices` metadata, scheduled runs (cron/systemd timer), a SQLite store.

---

## 4. Decisions needed from the user before execution

1. ✅ **Data categories: ALL** (decided 2026-09-16). Request all six read scopes: `activity_and_fitness`, `health_metrics_and_measurements`, `sleep`, `nutrition`, `ecg`, `irn` (each `.readonly`). `config.example.toml` default is `data_types = "all"`.
2. **How far back?** Default: 1 year. Full history just takes longer; the windows make it safe either way.
3. **Output format:** raw JSON only (default), or add flattened CSV per type now?
4. **Re-auth every 7 days** (Testing mode) is acceptable for personal use? The alternative is Google's restricted-scope verification, which isn't practical for a personal script.

## 5. Risks

- The API is new (2026). Data type names and filter fields may change, so Phase 2 step 5 checks them live.
- Rate limits aren't documented. Backoff plus 14-day windows should be enough. If you hit repeated 429s, add a delay between requests.
- The filter for `food`/`food-measurement-unit` isn't documented. These types may be reference data rather than time series.
