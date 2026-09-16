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
