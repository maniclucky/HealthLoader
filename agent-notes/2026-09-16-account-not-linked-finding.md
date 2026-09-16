**Summary:** OAuth sign-in succeeds and grants all six scopes, but the API rejects every call with `400 FAILED_PRECONDITION / ACCOUNT_NOT_LINKED` until the Google account is linked to a Google Health/Fitbit data account. This is a one-time account setup step, not a code or scope problem.
**Date:** 2026-09-16 · **Author:** Claude (Sonnet 5), executing the plan · **Related:** [2026-09-16-google-health-export-plan.md](2026-09-16-google-health-export-plan.md)

## What happened

After `fitthing auth` completed successfully (all 6 `googlehealth.*.readonly` scopes granted, token saved), `fitthing whoami` → `GET /v4/users/me/identity` returned:

```json
{
  "error": {
    "code": 400,
    "message": "The account is not linked to Google Health.",
    "status": "FAILED_PRECONDITION",
    "details": [{
      "@type": "type.googleapis.com/google.rpc.ErrorInfo",
      "reason": "ACCOUNT_NOT_LINKED",
      "domain": "health.googleapis.com",
      "metadata": { "redirect_uri": "https://fitbit.google.com/auth/signup" }
    }]
  }
}
```

This is a `400`, not a `401` — the token itself is fine. The Google account authorizing the app simply has no underlying Google Health/Fitbit data profile yet.

## Bug found and fixed as a result

`auth._check_identity()` originally called `resp.raise_for_status()` for any non-401 status, so this 400 crashed with a raw Python traceback instead of being handled by the calling command. Per the plan ("On 401 → sign-in required. Otherwise return creds."), only 401 should affect credential validity. Fixed: `_check_identity` now just returns `resp.status_code != 401` and lets the caller's own request see any other error. `cmd_whoami` (and future commands) print the API's JSON error body and exit 1 instead of raising. All 13 existing tests still pass unchanged.

## User action needed (blocking)

Visit `https://fitbit.google.com/auth/signup` while signed into the same Google account used for `fitthing auth`, and complete linking/creating a Google Health data profile. Then re-run `fitthing whoami`.

## Open question for Phase 2 step 5 (live filter verification)

Blocked until the account is linked — can't do live `dataPoints.list` calls against an account with no data. Resume that verification once `whoami` succeeds.

## Update: scope clarification (2026-09-16, same day)

The user's data comes from the **Google Fit app** (aggregating several third-party apps), not a Fitbit device. This matters: the Google Health API's own docs only describe migrating *Fitbit Web API* apps — Google Fit is a separate legacy product with its own, unrelated sunset track. Research found:

- Google's new consumer **Google Health app** (rollout began 2026-05-19) can connect to **Android Health Connect** — the on-device store most fitness apps, including current Google Fit, now write to. Linking that inside the Google Health app is the plausible way to satisfy `ACCOUNT_NOT_LINKED` with real data behind it.
- A **bulk historical Google Fit → Google Health migration tool** was promised "later in 2026" but had not shipped as of last search. Older multi-year history may not be available even after linking; only what's flowed through Health Connect might show up at first.
- The `fitbit.google.com/auth/signup` link from the API error reads as Fitbit-account creation and its relationship to Health Connect data is undocumented — did not test it blind to avoid creating a disconnected empty account.

**Action taken:** user installed the Google Health app; it is now syncing with their existing (Health Connect-backed) data. Once sync finishes, retry `fitthing whoami`. If it still 400s with `ACCOUNT_NOT_LINKED`, or succeeds but `dataPoints.list` comes back empty for known-populated types, that confirms the historical-migration-tool gap above rather than a code bug — re-check Google's changelog for the migration tool before assuming something is broken.

## Resolved (2026-09-16, same day)

`fitthing whoami` now succeeds: `{"name": "users/me/identity", "legacyUserId": "G8C9LH", "healthUserId": "3763766562200962175"}`. Installing the Google Health app and letting it sync Health Connect data was enough — no separate Fitbit account was created, and real data (steps, weight, heart rate, exercise, sleep) is queryable. Phase 2 is fully unblocked.

## Phase 2 step 5: live filter verification results

Tested directly against the real API with the user's own token (ad hoc script, not committed — see git history of this note's commit for the exact calls if needed). All confirmed **exactly as documented in the plan's §1 table**:

| Type tested | Kind | Filter used | Result |
|---|---|---|---|
| `steps` | Interval | `steps.interval.start_time >= "…" AND < "…"` (RFC-3339) | 200, real Health Connect data (`com.google.android.apps.fitness`) |
| `weight` | Sample | `weight.sample_time.physical_time >= "…" AND < "…"` | 200, real data from `FITBIT` platform (manual entry) |
| `daily-resting-heart-rate` | Daily | `daily_resting_heart_rate.date >= "YYYY-MM-DD" AND < "…"` | 200, real data from multiple source apps (Health Connect + a ring app) |
| `exercise` | Session (civil) | `exercise.interval.civil_start_time >= "…T00:00:00" AND < "…"` | 200, real workout session with full metrics |
| `sleep` | Session (sleep-specific) | `sleep.interval.end_time >= "…" AND < "…"` (RFC-3339) | 200, real sleep session with stages |
| `nutrition-log` | Session (civil) | `nutrition_log.interval.civil_start_time >= "…" AND < "…"` | 200, **empty** `{}` — filter syntax accepted, this account just has no nutrition-log entries. Not a bug. |
| **`food`** | — | none (tested with and without a filter — same result) | 200 regardless of filter. Returns a **global reference catalog** of branded food items with nutrient breakdowns (e.g. "Individual Chocolates, Delice"), not a per-user log. |
| **`food-measurement-unit`** | — | none | 200. Returns a **global reference catalog** of units (almond/almonds, anchovy/anchovies, …), not per-user data. |

**Pagination:** confirmed `nextPageToken` is the exact field name; a returned token works as `pageToken` on the next request and yields the next page. No surprises vs. the plan.

## Registry correction: `food` and `food-measurement-unit` are reference data, not logs

The plan's §1 table marked these "unknown, verify in Phase 2." Now verified: they are **not** time-series data the user logged — they're Google's global lookup tables (what a food/unit *is*, not what the user *ate*). They accept no date filter and return the same catalog regardless.

**Decision:** exclude `food` and `food-measurement-unit` from the default windowed export in `datatypes.py`/`export.py`. They don't fit "download my logged data" (the user's own stated goal) — exporting them would mean repeatedly re-downloading Google's entire food/unit database on every incremental run, which is large, not personal, and not something 14-day date windows apply to. `nutrition-log` (the actual per-user log) stays in scope, unchanged. This is a scope decision applying the user's already-stated intent to a newly-discovered fact, not a guess about API behavior — flagged to the user in the session, not silently done.
