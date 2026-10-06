# `fitthing sync`: both pulls + full-data daily.csv

2026-10-06, agent (Claude). Builds on [[2026-09-24-combined-daily-csv-gapfill]].

## What changed

- New `src/fitthing/sync.py` → `run_sync(config, interactive, full, days)`,
  exposed as `fitthing sync [--full] [--days N]`. Runs Cronometer **first**,
  then Google, so the CSV `run_export` rebuilds at its end already has the
  fresh nutrition. Each source is isolated: auth errors, `sys.exit(2)` from
  the sign-in helpers (caught as `SystemExit`), network and API errors are
  recorded on `SyncResult` and the other source still runs. If Google never
  reaches `run_export`, `run_sync` rebuilds the CSV itself. Exit code 1 if
  either source failed or any Google type failed.
- User asked for "all the available data" in the one CSV. Interpreted as:
  - **Every numeric Cronometer column.** `DailyNutrition.nutrients` carries
    all non-core numeric columns; `daily.json` entries gain a `nutrients`
    map. CSV appends them alphabetically after the fixed columns
    (`daily.json` is saved with `sort_keys`, so Cronometer's original column
    order isn't preserved anyway). Non-numeric `Completed` is dropped.
  - **Google types that actually have data on disk**: ~~distance~~ (removed
    same day, see [[2026-10-06-treadmill-steps]]), heart-rate (mean), daily-resting-heart-rate, daily-oxygen-saturation
    (`averagePercentage`), sleep (`summary.minutesAsleep`, summed, credited
    to the local date of `interval.endTime` + `endUtcOffset`), exercise
    (originally summed `activeDuration`; now an interval union, see
    [[2026-10-06-treadmill-steps]]).
  - Types with zero data points on disk (HRV, respiratory rate, VO2 max,
    hydration, etc.) were **not** added: their point schemas have never been
    observed, so an extractor would be guesswork. Add them the same way once
    data shows up.
- Header mojibake fixed in `parse.py`: Cronometer's export has no charset,
  so `requests` decodes as ISO-8859-1 and "µg" arrives as "Âµg". Repaired at
  parse time (raw CSVs on disk still contain the mojibake; byte-identical
  raw saving is tested, so the client wasn't changed).

## Decisions worth knowing

- ~~Distance is not device-filtered.~~ **Superseded**: the Distance column
  was removed. It double-counted (two phone sources mirror each other, and
  the treadmill uploaded one session twice), and the user cares about total
  steps, not distance. See [[2026-10-06-treadmill-steps]].
- No gap-filling for any new column (no interpolation, no targets).
  `targetSettings.json` has `steps`/`sleep` keys, but they are targets, not
  fill values, and were left alone, as before.

## Verified

- 139 tests pass (new: `tests/test_sync.py`, aggregate tests for each new
  column, parse tests for nutrients + mojibake).
- Re-imported the two raw Cronometer CSVs on disk (`cronometer-import`) so
  older `daily.json` days gained `nutrients`, then ran live
  `fitthing --no-prompt sync`: Cronometer pulled 15 days
  (2026-09-20→2026-10-05). **Google failed: "Sign-in required"**. The token
  was authorized 2026-09-24, 12 days earlier. Unverified guess: the OAuth
  app is in Testing status, where Google expires refresh tokens after 7
  days. Needs an interactive `fitthing auth`. daily.csv was still rebuilt:
  30 rows, 71 data columns; the new Google columns show plausible values for
  2026-09-21..23, the only days with raw data for them.
- Pre-run backup of `data/` was taken in the session scratchpad only (not
  persistent).

## Open

- If the 7-day expiry guess is right, every `sync` needs a re-auth weekly
  until the OAuth consent screen is published.
