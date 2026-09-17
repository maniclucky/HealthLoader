# Plan: Cronometer → local daily calories & macros

**Summary:** Add a second data source to `fitthing`: log into cronometer.com with the user's own
account, download the **Daily Nutrition Summary** CSV export, and keep a normalized
`data/nutrition/daily.json` of **date → calories, protein, carbs, fat** on disk, incrementally.
**Date:** 2026-09-17 · **Author:** Claude (Opus 5), planning · **Status:** PLANNED — not started.
**Executor:** a follow-up agent. Do the phases in order. Do not skip acceptance checks.

Scope decision from the user (2026-09-17): **daily calories and macros only** — not per-serving
food entries, not micronutrients, not exercises/biometrics/notes. Fetch method: **automated web
login**. Output: **raw CSV kept as-is, plus a trimmed daily JSON.**

---

## 0. Rules for the executing agent

1. **Cronometer has no public API for individual accounts.** Everything below rides on the
   endpoints their own web app uses. **Verify every request live before coding it** (Phase 0).
   If reality differs from this plan, stop, ask the user, and record what you found in a new
   `agent-notes/` note rather than guessing.
2. **Never commit secrets or health data.** `secrets/` and `data/` are already gitignored —
   confirm before writing a credentials file.
3. **Read-only.** Never POST anything to Cronometer except the login form. Do not touch the
   user's diary, servings, or settings.
4. **Respect their export cap: ~10 exports/day, personal use only.** This drives a core design
   decision — see §3.
5. Commit at the end of each phase. Run `pytest` before each commit. No network in tests.
6. Tasks marked **[USER]** need a human with a browser. Give exact instructions and wait.
7. At the end, add a handoff note in `agent-notes/` and update `agent-notes/INDEX.md`.

---

## 1. Background (researched 2026-09-17, **not yet verified live**)

Cronometer offers no individual API, but their web app's export endpoint is reachable with a
normal session. The flow below is how the open-source clients do it (primary reference:
[`jrmycanady/gocronometer`](https://github.com/jrmycanady/gocronometer), Go; several
`cronometer-mcp` Python ports exist). Treat all of it as **unconfirmed until Phase 0**.

### 1.1 Login → export nonce

| Step | Request | Notes |
|---|---|---|
| 1 | `GET https://cronometer.com/login` | Parse the HTML for `<input name="anticsrf" value="…">`. |
| 2 | `POST https://cronometer.com/login` | Form-encoded: `anticsrf`, `username`, `password`. Success sets a **`sesnonce`** cookie — that cookie *is* the session nonce. |
| 3 | `POST https://cronometer.com/cronometer/app` | GWT-RPC. Headers: `content-type: text/x-gwt-rpc; charset=utf-8`, `x-gwt-module-base: https://cronometer.com/cronometer/`, `x-gwt-permutation: <hash>`. Returns `//OK[<userId>,…` — regex out the numeric user id. |
| 4 | `POST https://cronometer.com/cronometer/app` | Second GWT-RPC call, `generateAuthorizationToken(sesnonce, userId)`. Response body carries the **export nonce** in quotes. Short-lived. |
| 5 | `GET https://cronometer.com/export?nonce=<token>&generate=dailySummary&start=YYYY-MM-DD&end=YYYY-MM-DD` | Returns the Daily Nutrition Summary as **CSV text**. Send `sec-fetch-dest: document`, `sec-fetch-mode: navigate`, `sec-fetch-site: same-origin`. |

**The two GWT-RPC request bodies are opaque serialized strings containing a permutation hash and
a policy-file hash that change whenever Cronometer redeploys their frontend.** They cannot be
guessed or constructed from first principles — they must be copied from a real browser session
and re-copied when they break. This is the single biggest fragility in this plan; §5 and Phase 1
make them user-overridable in `config.toml` so a break is a config edit, not a code change.

### 1.2 The CSV

`generate=dailySummary` yields one row per day. Export is available on **free and Gold** tiers.
Expected columns include `Date`, `Energy (kcal)`, `Protein (g)`, `Carbs (g)` (possibly spelled
`Carbohydrates (g)`), `Fat (g)`, plus ~80 other nutrients and a completeness flag.

**Do not hardcode these header names from this note.** Phase 0 captures the real header row from
the user's own account (their unit settings affect it — e.g. energy may come back in **kJ**, not
kcal). The parser then maps by a candidate-name list and **fails loudly, printing the actual
headers it saw**, if no candidate matches. A silently-zero macro column is the worst outcome
here and the mapping must make it impossible.

### 1.3 Relationship to the existing Google Health export

`nutrition-log` and `hydration-log` already come down from Google Health, and if the user's
Cronometer syncs into Health Connect the two overlap. They are not interchangeable: Google's
records are UTC-windowed per entry, Cronometer's are **local calendar days already summed by
Cronometer**. After this work, `data/nutrition/daily.json` is the canonical answer for "what did
I eat on day X" and the Google `nutrition-log` export stays as raw backup. Do not try to merge,
reconcile, or dedupe them — out of scope.

---

## 2. Target layout

```
src/fitthing/
  cronometer/
    __init__.py
    auth.py      # anticsrf -> login -> sesnonce -> userId -> export nonce; session cache
    client.py    # export endpoint + retry/backoff (mirrors client.py's policy)
    parse.py     # dailySummary CSV -> [DailyNutrition]  (pure, no network)
    export.py    # range logic, raw CSV write, daily.json merge, state
tests/
  test_cronometer_auth.py
  test_cronometer_parse.py
  test_cronometer_export.py
data/
  raw/cronometer-daily-summary/{start}_{end}.csv      # untouched download
  nutrition/daily.json                                # merged, trimmed
secrets/
  cronometer.json           # {"username": …, "password": …}, chmod 0600  (optional, see 3.2)
  cronometer_session.json   # cached cookies, chmod 0600
```

Everything Cronometer-specific lives under `src/fitthing/cronometer/`. **Do not modify
`client.py`, `datatypes.py`, `windows.py`, or `export.py`** — those are the Google Health path and
this source shares none of their assumptions (no OAuth, no scopes, no AIP-160 filters, no
14-day windows). Shared ideas (retry/backoff schedule, atomic tmp+`os.replace` writes, state
keyed by source id) are re-used by *copying the small pattern*, not by extracting a premature
abstraction over two very different sources.

### 2.1 `data/nutrition/daily.json`

One object keyed by ISO date, sorted, so it diffs cleanly and is trivial to read from anything:

```json
{
  "2026-09-15": { "calories_kcal": 2103.4, "protein_g": 150.2, "carbs_g": 180.0, "fat_g": 70.1 },
  "2026-09-16": { "calories_kcal": 1890.0, "protein_g": 141.7, "carbs_g": 155.3, "fat_g": 62.8 }
}
```

Merge semantics: a re-export **overwrites** any date it contains and leaves other dates alone.
That is what makes the overlap re-fetch in §3 self-correcting when a day is edited after the fact.
Written atomically (tmp + `os.replace`), same as `save_state`.

### 2.2 State

Reuse `data/state.json` with the key `cronometer-daily-summary` →
`{"synced_through": "YYYY-MM-DD", "last_run": "<iso8601>"}`. The key deliberately cannot collide
with a Google `DataType.id`. One state file for the whole tool.

---

## 3. Key design decision: one request per run, not windows

The Google path splits a year into 14-day windows (~800 requests for a full backfill). **Doing
that here would blow through Cronometer's ~10-exports-per-day cap on the first run.** The export
endpoint accepts an arbitrary `start`/`end`, so:

- **First run / `--full`:** one request covering the entire backfill range (default 365 days back,
  or `start_date` from config, through today). One export. One CSV file on disk.
- **Every later run:** one request covering `[synced_through − 3 days, today]`, matching the
  existing `STATE_OVERLAP_DAYS = 3` rationale — days get edited late. One export.
- **Never loop.** If a range is genuinely huge, it is still one request; the CSV is small (one row
  per day, not per food).

So a normal day costs **1 of ~10** exports. The client must still pace and back off (Cronometer is
described as having aggressive login rate limiting), and must **cache the session cookie** so a
rerun does not re-login every time.

### 3.1 Config additions (`config.toml` / `config.example.toml`)

```toml
[cronometer]
# Automated download from cronometer.com. Unofficial endpoints — see
# agent-notes/2026-09-17-cronometer-nutrition-plan.md.
enabled = true
credentials_path = "secrets/cronometer.json"
session_path = "secrets/cronometer_session.json"
# First run backfills from here (YYYY-MM-DD); unset = today minus 365 days.
# start_date = "2025-09-17"
# These two are copied out of a real browser session and change when Cronometer
# redeploys their web app. If export starts failing, refresh them (Phase 0).
gwt_module_base = "https://cronometer.com/cronometer/"
gwt_permutation = ""   # filled in by Phase 0
request_delay_seconds = 1.0
```

`config.py` grows a nested `CronometerConfig` dataclass parsed from the `[cronometer]` table, with
its own defaults dict, mirroring the existing `DEFAULTS`/`Config` style. `Config` gets a
`cronometer: CronometerConfig` field. **Existing configs without a `[cronometer]` table must keep
working** — defaults fill in, `enabled` defaults to `false` so nothing changes for anyone who
hasn't set it up.

### 3.2 Credentials

Password order of precedence, first hit wins:
1. `CRONOMETER_USERNAME` / `CRONOMETER_PASSWORD` environment variables (nothing stored on disk).
2. `secrets/cronometer.json`, created by `fitthing cronometer-auth` with `getpass` (never echoed,
   never in argv, never logged), written `chmod 0600`.
3. Interactive prompt, if a TTY and `--no-prompt` is not set — matching how `ensure_signed_in`
   already treats interactivity.

Non-interactive with no credentials: print `Cronometer sign-in required. Run: fitthing
cronometer-auth` and exit **2**, exactly like the Google path's contract.

**If the account has 2FA enabled, scripted login will not work.** Detect it (login POST returns
a challenge rather than setting `sesnonce`), say so plainly, and point at the manual fallback
(Phase 3, item 5). Do not attempt to defeat it.

---

## 4. Phases

### Phase 0: live recon [USER] — capture the real requests *before writing any code*

Nothing in §1 is verified. This phase exists so the implementation is written against observed
reality, not against this note.

1. The user signs in to cronometer.com in Chrome with DevTools → Network open (or the agent
   drives it with the `claude-in-chrome` skill, with the user's permission — the user types the
   password themselves either way; **the agent must never handle it in a browser automation
   step**).
2. Capture, as raw text: the login `POST` (form fields + response `Set-Cookie`), both
   `/cronometer/app` GWT-RPC `POST`s (**full request bodies verbatim**, plus the
   `x-gwt-permutation` and `x-gwt-module-base` headers), and the resulting `/export?...` URL.
3. Do **one** manual Daily Nutrition Summary export over a 3-day range and save the CSV to
   `/tmp` (not into the repo) — this is the parser's fixture source.
4. Record in a new note `agent-notes/2026-09-17-cronometer-endpoints.md`: the verbatim GWT bodies
   with the nonce/userId positions marked as `%s` placeholders, the permutation hash, the exact
   CSV header row, and which energy unit the account is set to. **Redact the session cookie and
   any nonce values — they are credentials.**
5. Copy 3 rows of the CSV into `tests/fixtures/cronometer_daily_summary.csv` **with the numbers
   replaced by made-up values** — a test fixture must not contain the user's real intake.

**Acceptance:** the note exists, has the two GWT bodies templated, names the real headers, and
`config.toml`'s `gwt_permutation` is filled in. If any step fails (endpoint changed, 2FA, login
blocked), stop and report to the user before Phase 1.

### Phase 1: config + package scaffolding

`src/fitthing/cronometer/__init__.py`, the `CronometerConfig` dataclass, `[cronometer]` in
`config.example.toml`, credential resolution per §3.2.

**Acceptance:** `pytest` green, including new tests that (a) a config with no `[cronometer]`
table loads with `enabled = false` and correct defaults, (b) env vars beat the secrets file,
(c) the secrets file is written `0600`.

### Phase 2: `cronometer/auth.py` + `fitthing cronometer-auth`

Implements §1.1 steps 1–4 against the Phase 0 templates. Session cookies cached to
`session_path`; on a run, try the cached session first and only re-login if the export call comes
back unauthenticated (one retry, then fail). New CLI subcommand `cronometer-auth`: prompts,
logs in, mints a nonce, prints `Signed in to Cronometer as <username>.` and saves the session.

**Acceptance:** `fitthing cronometer-auth` works live once. Tests (with `responses`, already a
dev dep) cover: anticsrf extraction from a saved HTML snippet; login failure → clear message,
exit 2; a GWT response that is not `//OK[...` → an error that names the permutation hash and
points at the recon note; cached session reused without a second login POST.

### Phase 3: `client.py` + `parse.py`

1. `client.py`: `export_daily_summary(start, end) -> str` (CSV text). Retry/backoff mirroring
   `client.py`'s `RETRY_STATUSES`/`BACKOFF_SCHEDULE_SECONDS`, honoring `Retry-After`, plus the
   `request_delay_seconds` pacing. Detect an HTML login page returned in place of CSV (that is
   how an expired session shows up) and surface it as "session expired", not as a parse error.
2. Detect the daily-export cap: if the response says the limit is hit, **do not retry** — say
   `Cronometer's daily export limit (~10/day) is reached; try again tomorrow.` and exit non-zero.
3. `parse.py`: `parse_daily_summary(csv_text) -> list[DailyNutrition]` where `DailyNutrition` is
   a frozen dataclass of `date, calories_kcal, protein_g, carbs_g, fat_g`. Header mapping via a
   candidate-name list per field (`"Energy (kcal)"`, `"Energy"`, `"Calories"`, …). **Unmatched
   required column → `ValueError` listing every header actually present.** kJ → kcal conversion
   (÷ 4.184) if and only if the header says kJ, with the conversion logged once.
4. Blank/missing cells → `None`, not `0.0`, so "didn't log" is distinguishable from "ate nothing".
   Rows outside the requested range, or with an unparseable date, are dropped with a warning.
5. **Manual fallback** (small, and it falls out of the parser for free): `fitthing
   cronometer-import <file.csv>` runs the same parse + merge on a CSV the user exported by hand.
   This is the break-glass path for when the GWT bodies break or 2FA blocks login. The user chose
   automated fetching, so this is deliberately just a thin CLI wrapper — strike it if unwanted.

**Acceptance:** parser tests against the redacted fixture, plus: kJ account, missing macro column,
empty export, a day with blanks, CRLF line endings, and a BOM (exports commonly carry one).

### Phase 4: `cronometer/export.py` + `fitthing cronometer-export`

Range logic per §3, raw CSV written to `data/raw/cronometer-daily-summary/{start}_{end}.csv`,
merge into `data/nutrition/daily.json` per §2.1, state updated per §2.2. Flags mirroring the
Google path exactly: `--since`, `--until`, `--full`. Summary line to stderr:
`Cronometer: N day(s) updated, range X→Y.`

**Acceptance:** tests for first-run range (365 back), incremental range (3-day overlap), `--full`,
`--since/--until` overrides; merge upserts and leaves untouched dates alone; the raw CSV is
written byte-identical to what the client returned; a crash after download but before merge
leaves state unadvanced (so a rerun is correct). Then **one live run**, and eyeball
`daily.json` against what Cronometer's web UI shows for 2–3 days — this is the only check that
catches a wrong column mapping.

### Phase 5: docs & handoff

README section (setup, the 10/day cap, what breaks when Cronometer redeploys and how to fix it
via Phase 0), a handoff note, `INDEX.md` updated.

### Deferred (not in this plan unless the user asks)

Per-serving/food-level detail, micronutrients, fiber/sugar/sodium, Cronometer biometrics or
exercises (weight and activity already come from Google Health), CSV→any other output format,
reconciling Cronometer against Google's `nutrition-log`, and folding Cronometer into the main
`fitthing export` command. **Note:** if the user later wants fiber or sugar, it is a one-line
addition to the parser's field list — but the raw CSVs are kept precisely so that does not
require re-downloading anything.

---

## 5. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| **GWT permutation / RPC body changes on a Cronometer redeploy** — export stops working | High, eventually | Both values live in `config.toml`, not code. Error message names them and points at the recon note. `cronometer-import` is the break-glass path. |
| Wrong CSV column mapped → plausible-looking but wrong macros | Medium | Mapping fails loudly instead of defaulting; Phase 4 requires eyeballing 2–3 days against the web UI. |
| Energy in kJ (or another locale/unit setting) silently read as kcal | Medium | Unit detected from the header text; conversion explicit and logged. |
| ~10 exports/day cap hit while developing | Medium | One request per run by design; all tests offline against the fixture; the cap response is detected and not retried. |
| Account has 2FA → scripted login impossible | Medium | Detected and reported plainly; fall back to `cronometer-import`. |
| Password stored on disk | Certain, if the user picks that option | Env vars supported as the no-storage path; file is `0600` under already-gitignored `secrets/`; `getpass` so it never hits shell history or argv. |
| Terms-of-service / unofficial-endpoint gray area | — | Personal use, the user's own data, read-only, one request per run, well inside the documented export cap. Flagged here so the user is making an informed choice. |
| Cronometer's "day" is a local calendar day; Google's records are UTC-windowed | Certain | Documented in §1.3; no merging attempted. |
