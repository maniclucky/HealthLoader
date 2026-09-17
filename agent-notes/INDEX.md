# agent-notes index

One line per note: `- [Title](file.md) — one-line summary`

- [Google Health export plan](2026-09-16-google-health-export-plan.md) — phased build plan for the Python Google Health API v4 export CLI (all 5 phases built and tested; see handoff note)
- [Account-not-linked finding](2026-09-16-account-not-linked-finding.md) — API needs the Google account linked to Google Health/Fitbit before any call works; live filter verification results; food/food-measurement-unit are reference catalogs, excluded from export
- [Phase 5 handoff](2026-09-16-phase-5-handoff.md) — build complete, tests passing (see rate-limit finding for the corrected count), what was live-verified, what's still untested (the real 1-year backfill), known gaps
- [Rate-limit finding](2026-09-16-rate-limit-finding.md) — real backfill hit Google's rate limit partway through; fixed with request pacing + per-type failure isolation (deviation from original plan's "other errors stop the run" wording, documented); rerunning `fitthing export` resumes correctly, no manual cleanup
