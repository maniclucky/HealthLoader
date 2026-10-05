# Default backfill changed from 365 to 30 days, with --days override

2026-09-21, agent (Claude).

Both `export` and `cronometer-export` used the same pattern: on a fresh
run (no prior state, no `--since`, no `config.start_date`), backfill
`DEFAULT_BACKFILL_DAYS` before the end of the range. That constant was
365 in both `fitthing/export.py` and `fitthing/cronometer/export.py`.

Changed at user request: default is now 30 days, with a new `--days N`
CLI flag (both commands) to widen a fresh backfill up to
`MAX_BACKFILL_DAYS` (kept at 365, the old default — the CLI rejects
anything outside `[1, 365]`). `range_for()` in both modules now takes an
optional `days` param; it's ignored once `config.start_date` (or
`cronometer.start_date`) is set, since that's a more specific override —
same precedence order as before, just with `days` slotted in above the
bare `DEFAULT_BACKFILL_DAYS` constant.

Scope: applied to both exporters identically, since they share the exact
same default-backfill pattern (confirmed with the user rather than
guessing — see conversation). `--since`/`--until`/`--full` behavior is
unchanged.

Updated: `config.example.toml` comments, `README.md` (both export
sections + flag lists), and existing tests that hard-coded the 365-day
default (`test_first_run_backfills_default_365_days` in both test files,
renamed to `..._30_days`, plus new tests for the `--days` override and
for `start_date` still winning over `--days`).
