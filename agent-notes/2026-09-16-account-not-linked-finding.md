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
