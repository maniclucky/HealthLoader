# daily.csv: merged nutrition + fixed 30-day window + gap-filling

2026-09-24, agent (Claude). Follows on from
[[2026-09-24-google-daily-csv]] — read that first.

## What changed, and why

The user reported `data/health/daily.csv` only had 3 rows. Root cause:
between the previous session and this one, every `data/raw/<type>/`
directory had been reduced to a single window file
(`2026-09-21_2026-09-24.json`, all types, all with the same `fetched_at` —
so a real external event, not something this session did). `state.json`
still had `synced_through: "2026-09-24"` from before, so a plain
`fitthing export` only pulled the 3-day incremental overlap catch-up —
correct incremental behavior, but the historical raw window files that
used to cover the rest of the 30-day range were simply gone from disk.
Cronometer's `data/nutrition/daily.json` was untouched (still had 25 days)
because it's a persisted merge, not a rescan of raw files — the Google
side's design (full rescan every run, see the other note) is more fragile
to raw-file loss than Cronometer's. Not fixed here — noted as a gap below.

Rather than treat that as a one-off "go re-run a backfill," the user asked
for the aggregate CSV itself to be resilient to sparse raw data: always
show up to 30 days, and gap-fill what's missing. Three explicit asks:
1. Up to 30 days, not just whatever happens to be on disk.
2. Interpolate missing weight/body fat.
3. Fill missing nutrition from `targetSettings.json`.

## Design

`aggregate.py`'s `build_combined_rows()` now:
- Always emits exactly `config.csv_days` (new config key, default 30) rows
  — one per calendar day in `[end - csv_days + 1, end]`, `end` defaulting
  to yesterday (today excluded, same convention as everywhere else in this
  codebase). A day with literally nothing in any source still gets a row,
  all-blank, so `write_daily_csv` is no longer "N days that have data" but
  "csv_days days, filled in as well as possible."
- Merges in Cronometer's `data/nutrition/daily.json` via
  `fitthing.cronometer.export.load_daily_json` (reused, not
  reimplemented) as 4 new columns: `Energy (kcal)`, `Protein (g)`, `Carbs
  (g)`, `Fat (g)` — first in column order, matching the user's own
  `output.csv`/`dev.ipynb` prototype's layout.
- Interpolates `Weight (lbs)`/`Body Fat (%)` gaps with a small pure-Python
  linear interpolator (`_interpolate()`) — no pandas dependency introduced,
  since this is the only place in the codebase that would need it. Matches
  pandas' default `.interpolate()` semantics: only fills a gap with a known
  value on *both* sides; a leading/trailing gap is left `None`.
- Fills nutrition gaps from `target_settings_path` (new config key,
  default `targetSettings.json` at repo root — the user's existing
  untracked file, now referenced by path instead of copied/moved) — but
  only where Cronometer logged literally nothing that day; a logged day
  is never partially overridden even if e.g. only carbs are present, since
  the merge is at the day level (`daily.json` doesn't currently store
  partial-day macros).

This is the "combined Google+Cronometer CSV" option the user explicitly
declined in the first round (see [[2026-09-24-google-daily-csv]]'s
AskUserQuestion) — now built because the user asked for it directly in a
follow-up, not re-litigated or re-asked.

## Verified

- `tests/test_aggregate.py`: 18 tests total, added ~10 covering the fixed
  window (blank-everywhere with zero data), nutrition merge, target-fill
  (including "logged day isn't overridden" and "no target file leaves it
  blank"), interpolation (interior gap + both leading and trailing edges
  left blank), and that steps/energy-burned are never filled.
- Ran `write_daily_csv()` against the real (sparse) `data/` on disk: 30
  rows, nutrition columns correctly target-filled for the ~27 days with no
  Cronometer entry and left as logged for the rest; weight/body-fat
  correctly stayed blank for all 27 sparse days — not a bug: all 3 known
  weight readings are clustered at the *end* of the window with nothing
  after them, so there's no interior gap to interpolate, only one long
  leading gap. Flagged to the user in-conversation so "why didn't
  interpolation fill anything" isn't a surprise later.

## Known gaps

- The root cause of the raw-file loss itself is still open — not
  diagnosed further (no git history for `data/`, it's gitignored) and not
  fixed. If it recurs, a `fitthing export --full --days 30` repopulates
  the raw JSON (Google-side rate limits permitting); the CSV's own
  resilience (this change) only compensates for gaps in the *output*, not
  in the underlying raw archive.
- Worth considering later: making the Google side persist its own merged
  daily state (like `daily.json`) instead of a pure rescan-of-raw-files,
  so it wouldn't be vulnerable to raw-file loss the way it just was.
  Deliberately not done here — bigger scope than what was asked.
- `nutrition_g`-level partial-day fill (e.g. protein logged but not fat)
  isn't possible without changing what `daily.json` stores; not attempted.
