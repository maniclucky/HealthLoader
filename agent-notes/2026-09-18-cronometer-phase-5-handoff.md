**Summary:** All 5 phases of the Cronometer daily-macros plan are built, tested, and live-verified against the real account. `fitthing cronometer-export` is working.
**Date:** 2026-09-18 · **Author:** Claude (Sonnet 5), executing the plan · **Related:** [2026-09-17-cronometer-nutrition-plan.md](2026-09-17-cronometer-nutrition-plan.md), [2026-09-17-cronometer-endpoints.md](2026-09-17-cronometer-endpoints.md)

## What's built

All 5 phases from the plan are done:

0. **Recon** — the real login/authenticate/generateAuthorizationToken GWT-RPC flow and CSV header, captured live and documented in the endpoints note (§0 corrected a few wrong assumptions from the original plan's un-verified §1, notably: there is no unpredictable opaque session cookie, it's a fixed-name `sesnonce`; `userCode` is sent blank).
1. **Config + credentials** — `[cronometer]` table in `config.toml`/`config.example.toml`, `CronometerConfig`, credential resolution (env vars → secrets file → interactive prompt) in `cronometer/auth.py`.
2. **Live sign-in** — `cronometer/auth.py`'s login → authenticate → generateAuthorizationToken chain, session caching (`secrets/cronometer_session.json`, 0600), `fitthing cronometer-auth`.
3. **Client + parser** — `cronometer/client.py` (one request per call, cached-session reuse with one re-login retry, retry/backoff, export-cap detection) and `cronometer/parse.py` (candidate-name column matching, kJ→kcal, blank→None).
4. **Export orchestration** — `cronometer/export.py` (single-request range logic, never windowed; raw CSV + `daily.json` merge; state), `fitthing cronometer-export` / `cronometer-import`.
5. **Docs** — README §5, this handoff note.

**106 automated tests, all passing, no network calls.** Run with `pytest`.

## What was verified live (real account, real data)

- `fitthing cronometer-auth` — real login, real `authenticate`/`generateAuthorizationToken` RPC calls, session saved and reused.
- `fitthing cronometer-export --since 2026-09-12 --until 2026-09-18` — real CSV downloaded, parsed, merged into `data/nutrition/daily.json`. The user eyeballed the result against Cronometer's own web UI.
- The real CSV header row and this account's energy unit (kcal) — see the endpoints note.

## Bugs found and fixed during live verification (not caught by mocked tests)

- **`CookieConflictError` on `session.cookies.get("sesnonce")`.** Cronometer's `authenticate` RPC response re-sets `sesnonce` scoped to a different cookie domain than what was already in the jar, so the jar ends up with two `Cookie` objects named `sesnonce` — `requests`' domain-less `.get()` refuses to pick one. Fixed with `_resolve_duplicate_cookies()` (prefers the longest-lived cookie per name), used both when reading `sesnonce` and when persisting the session to disk. Regression test in `test_cronometer_auth.py` reproduces the exact conflict via `requests.cookies.create_cookie`.
- **`cronometer-auth` didn't persist credentials.** The plan's §3.2 says it should write `secrets/cronometer.json` (so later `cronometer-export` runs don't re-prompt); the first implementation only saved the session. Fixed — skipped when credentials came from `CRONOMETER_USERNAME`/`PASSWORD` env vars, since that path is meant to leave nothing on disk.

Neither of these was reachable by the mocked `responses`-based tests, since both are about real cookie-jar/session-persistence behavior against the live server. Worth remembering next time a `responses`-mocked test suite says "green" for anything touching `requests.Session` cookie state — mocks don't reproduce real `Set-Cookie` domain-scoping quirks.

## Also fixed in this session, applies to both Google and Cronometer

**Today's date is now always excluded from both export paths.** The user asked for this after the Cronometer live run: a day still being logged is inherently incomplete data, so exporting it produces a plausible-looking but wrong "final" number for today. `fitthing/export.py`'s `range_for` (Google, UTC) and `fitthing/cronometer/export.py`'s `range_for` (Cronometer, local date — matching that path's existing local-calendar-day semantics) both clamp their effective end date to yesterday, even if `--until` explicitly names today or a future date. Self-corrects automatically on the next run once the excluded day is actually over, via the existing 3-day state overlap.

One residual: the user's live `cronometer-export` run above happened *before* this fix and covered through today (2026-09-18), so that one entry in `daily.json` may be incomplete. No action needed — the next run will re-fetch and overwrite it once that day is over.

## Known gaps / things to watch

- **The export-cap-reached detection is unverified.** `_looks_like_cap_reached()` in `client.py` guesses at Cronometer's wording ("export limit" / "too many exports") since triggering the real cap (~10/day) wasn't worth spending on during development. If the guess is wrong, the fallback behavior is still safe: `parse_daily_summary` fails loudly (naming missing headers) rather than silently producing zeros, so a cap-hit response would surface as a clear parse error, not corrupted data — just with a less helpful message than intended. Worth fixing the wording for real the next time the cap is actually hit.
- **The `authenticate`/`generateAuthorizationToken` policy hash** (`13F6CC6C06AE73A6E95DE9B0233AB365`) is asserted to be shared between the two calls based on GWT-RPC's per-{service,permutation} hashing behavior, not independently re-verified for the `authenticate` call specifically (it was live-verified as part of the *combined* `cronometer-auth` flow, which calls both). If Cronometer ever splits these across different permutations, that assumption would need revisiting — the endpoints note flags this.
- **2FA accounts are untested** — this account doesn't have 2FA enabled. `cronometer-import` (manual CSV) is the documented fallback; nobody has exercised it end-to-end against a real hand-exported file.
- **`[cronometer].enabled` doesn't gate anything yet.** It exists in config (defaults `false`) but `cronometer-auth`/`cronometer-export` are explicit opt-in subcommands, not folded into `fitthing export` — matches the plan's "Deferred" list, not a bug.

## Deferred (unchanged from the plan, not built)

Per-serving/food-level detail, micronutrients, fiber/sugar/sodium, Cronometer biometrics/exercises, reconciling Cronometer against Google's `nutrition-log`, folding Cronometer into the main `fitthing export` command.
