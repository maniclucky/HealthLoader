**Summary:** All 5 phases of the Google Health export plan are built, tested, and working against the real account. `fitthing export` is ready for the user's first real 1-year backfill.
**Date:** 2026-09-16 · **Author:** Claude (Sonnet 5), executing the plan · **Related:** [2026-09-16-google-health-export-plan.md](2026-09-16-google-health-export-plan.md), [2026-09-16-account-not-linked-finding.md](2026-09-16-account-not-linked-finding.md)

## What's built

All 5 phases from the plan are done:

1. **Scaffolding** — src-layout package, `pyproject.toml`, CLI skeleton, config loader.
2. **Auth** — `ensure_signed_in()` handles the full sign-in/refresh/re-auth-prompt lifecycle; `fitthing auth` and `fitthing whoami` work against the real account.
3. **API client** — `datatypes.py` registry (34 types), `windows.py` (date chunking + AIP-160 filters), `client.py` (pagination + retry/backoff). Live-verified against real data.
4. **Export** — `fitthing export`/`fitthing types`, idempotent incremental sync, atomic file writes, per-window state persistence. Live-verified in an isolated scratch directory (see below) — the real `data/` was never touched by that test.
5. **Docs** — README rewritten with full setup, usage, and output-format docs.

**61 automated tests, all passing, no network calls.** Run with `pytest`.

## What was verified live (real account, real data)

- `fitthing auth` / `fitthing whoami` — real sign-in, real identity returned.
- All documented filter fields (interval/sample/daily/session-civil/sleep/ecg) — confirmed exactly as the plan's §1 table describes, against real data of each kind.
- Pagination (`nextPageToken`/`pageToken`) — confirmed field names and round-trip behavior.
- The full `client.py` → `datatypes.py` → `windows.py` chain — real point counts returned for steps/weight/sleep/exercise/daily-resting-heart-rate.
- The full `export.py` pipeline — ran for real into an isolated scratch directory (not the project's `data/`), correct files + `state.json` + progress log lines, then cleaned up. See git history around commit `7fd2051` for the exact scratch-test steps if you need to repeat it.

## What's still genuinely untested: the real first backfill

Nobody has run `fitthing export` against the real `data/` directory yet. That's intentional — the plan's Phase 4 acceptance explicitly calls this a `[USER]` step (the user reviewing real output), and doing a full 365-day backfill blind risked writing `state.json` entries that would then silently prevent a *real* full backfill later (since a rerun without `--full` only does incremental sync once state exists for a type).

**Next action for the user:** run `fitthing export` for real (it will backfill ~1 year for every granted type — this may take a while and make a lot of API calls) and skim a few of the resulting files in `data/raw/*/`.

## Known gaps / things to watch

- **`nutrition-log`** returned empty in live testing — not a bug, this account just hasn't logged anything there. Worth a second look once real data exists.
- **Rate limits are undocumented.** The 429/5xx backoff (Phase 3) hasn't been exercised by an actual 429 from Google — only tested with mocks. If a real full backfill hits sustained 429s, it will retry per the plan (5 retries, up to ~31s of backoff) and then raise `ApiError`, stopping the run with state saved up to the last good window — safe to just rerun.
- **`--no-prompt` and non-interactive detection** — untested against an actual cron/systemd timer; only unit-tested with mocked `isatty`. Not needed yet since the plan explicitly deferred scheduled runs.
- **Historical depth ceiling** — unknown how far back Health Connect-sourced data actually goes for this account (the Google Fit → Google Health migration tool mentioned in the account-linking finding hadn't shipped as of this session). The 365-day backfill will simply return whatever exists; older gaps aren't a code issue.

## Deferred (unchanged from the plan, not built)

Flattened CSV/Parquet exports, `dailyRollUp` summaries, `pairedDevices` metadata, scheduled runs (cron/systemd timer), a SQLite store.
