**Summary:** The user's real first backfill hit Google's (undocumented) rate limit and aborted partway through, because the export had zero pacing between requests and one type's failure used to kill the whole run. Both fixed; the incremental-sync design (already built in Phase 4) resumes correctly on rerun with no manual cleanup needed.
**Date:** 2026-09-16 · **Author:** Claude (Sonnet 5), executing the plan · **Related:** [2026-09-16-google-health-export-plan.md](2026-09-16-google-health-export-plan.md), [2026-09-16-phase-5-handoff.md](2026-09-16-phase-5-handoff.md)

## What happened

The user ran `fitthing export` for real (the deferred Phase 4 `[USER]` step) and hit the rate limit. Inspecting the resulting `data/state.json` and `data/raw/`:

- **29 of 34 granted types fully backfilled** (27 windows each, full year).
- **`sleep` stopped at 15/27 windows** (`synced_through: "2026-04-15"`).
- **`hydration-log`, `nutrition-log`, `electrocardiogram`, `irregular-rhythm-notification` never started at all** — no state entry for any of them.

Timestamps in `state.json` showed ~29 types processed in roughly 2-3 minutes — hundreds of requests with zero delay between them. `sleep`'s small page size (25, vs. 10,000 for most types — see the plan's §1 table) means it needs many more paginated requests to cover the same span, which is almost certainly why it was the one that finally tipped into sustained rate limiting.

## Two problems, both fixed

1. **No pacing at all between requests.** The plan's own §5 Risks section anticipated this exact failure mode ("if you hit repeated 429s, add a delay between requests") but the code never implemented it. Fixed: `HealthClient` now takes `request_delay_seconds` and pauses that long before every attempt (retries included). Wired to a new `config.toml` key, default `0.5`. Live-verified: re-ran 150 days of `sleep` (the exact type that failed, at similar-or-greater per-window volume) with the fix — 23 seconds, zero errors.

2. **A design gap, not just bad luck: one type's exhausted retries used to abort the entire run.** The original plan text said "Other errors stop the run with exit code 1," written before real rate-limit behavior was known. In practice this meant `sleep` failing partway through the registry order meant every type after it (`hydration-log` onward) never even got attempted, even though they had nothing to do with `sleep`'s problem. **Deviation from the plan, made and documented rather than silently guessed:** `run_export()` now catches any `ApiError` per type, logs it, records it as failed, and continues to the next type. `export_data_type()`'s own state-saving (after every window, not just at the end) already made this safe — a failed type's state simply reflects wherever it got to, ready to resume.

Both changes are covered by new tests (`test_request_delay_seconds_paces_every_attempt`, `test_run_export_isolates_a_failing_type_and_continues`, plus a default-pacing-is-zero regression test). 51 tests total, all passing.

## What the user needs to do

Nothing manual — just rerun the same command:

```bash
fitthing export
```

Because of how Phase 4's incremental sync already works, this single rerun will:
- do a cheap 3-day overlap check for the 29 already-completed types (fast, low risk of re-triggering the limit),
- resume `sleep` from `2026-04-15` (minus the 3-day overlap) through today,
- run a fresh full 1-year backfill for `hydration-log`, `nutrition-log`, `electrocardiogram`, and `irregular-rhythm-notification` (they have no state yet).

If it still hits the rate limit somewhere, `data/state.json` shows exactly where progress stopped, and raising `request_delay_seconds` in `config.toml` (e.g. to `1.0` or `2.0`) is the next lever before rerunning again — no code changes needed.
