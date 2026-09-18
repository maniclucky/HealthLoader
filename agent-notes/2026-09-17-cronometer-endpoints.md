**Phase 2 live-verified 2026-09-18:** `fitthing cronometer-auth` ran against the real site and
succeeded end-to-end — login, `authenticate`, and `generateAuthorizationToken` all worked with
the templates below exactly as captured, confirming the policy-hash inference (same hash for
both RPC calls) was correct. `secrets/cronometer_session.json` was created with `sesnonce` plus
`AWSALB`/`AWSALBCORS`/`JSESSIONID`.

# Finding: Cronometer login flow — Phase 0 recon (COMPLETE)

**Date:** 2026-09-17 → 2026-09-18 · **Author:** Claude (Sonnet 5), with the user driving Firefox
DevTools · **Status:** Acceptance criteria met — both GWT bodies templated, real CSV headers
named, `gwt_permutation` captured. Two nice-to-have gaps remain, noted at the bottom, neither
blocking Phase 1.

This is Phase 0 of [2026-09-17-cronometer-nutrition-plan.md](2026-09-17-cronometer-nutrition-plan.md).
Its §1 assumptions (based on `jrmycanady/gocronometer`) are **confirmed stale in several
places** — see below. Do not code against §1 as written; use this note instead, and finish
capturing what's still missing before writing `cronometer/auth.py`.

## Confirmed 2026-09-17: login POST shape

- **Login is still a plain form POST** to `cronometer.com/login`,
  `application/x-www-form-urlencoded`. Response: **200, HTML body** (not a redirect, not JSON).
- **Request payload fields:** `username`, `password`, `userCode`, `anticsrf`.
- **GWT is still in use.** A `*.nocache.js` GWT bootstrap file loads after login, and POST
  requests to `cronometer.com/cronometer/app` fire a few seconds after — mixed in with many
  other RPC calls for dashboard widgets (see the method-name list below for what that noise is).

## Confirmed 2026-09-18: login response cookies, `userCode`, and the post-login RPC

Captured via a full HAR export of a fresh logout → login cycle (Firefox, saved via "Save All as
HAR"). **Superseded finding from 2026-09-17 below: there is no unpredictable opaque session
cookie** — that was a stale pre-login cookie the login response explicitly clears. The real
answer is simpler:

- **`userCode` sent blank (`userCode=`) and login succeeded normally.** It's optional/safe to
  leave empty — do not send a guessed value. Open question from 2026-09-17 **resolved**.
- **`anticsrf`** is a 32-char token, scraped from the login page as the plan assumed — value not
  reconfirmed byte-for-byte but presence/length confirmed.
- **Login response `Set-Cookie` headers**, name/attributes only (values are credentials, never
  recorded):
  - `AWSALB`, `AWSALBCORS` — AWS load-balancer stickiness. Not session-related, ignore (per
    2026-09-17 finding, still holds).
  - **`sesnonce`** — `Max-Age=1209600` (14 days), `Path=/`, `Secure`, `HttpOnly`, `SameSite=Lax`.
    **This is the real, fixed-name session cookie.** `auth.py` can key off this name directly —
    no need for the by-elimination approach the 2026-09-17 finding assumed.
  - One 32-char hex-named cookie set with `Max-Age=0` (immediately expired) — this is what the
    2026-09-17 session mistook for the real session cookie. It's a stale pre-login anonymous-
    session cookie that login explicitly clears. **Ignore it — it is not the session token.**
- **The post-login GWT-RPC call is named `authenticate`**, not something export-specific:
  - Request body template: `7|0|5|https://cronometer.com/cronometer/|13F6CC6C06AE73A6E95DE9B0233AB365|com.cronometer.shared.rpc.CronometerService|authenticate|java.lang.Integer/3438268394|1|2|3|4|1|5|5|%TZ_OFFSET_MINUTES%|` — the only variable part is the trailing integer, the browser's local timezone offset in minutes (e.g. `-300` for EST). No sesnonce/userId placeholder needed — auth rides the `sesnonce` cookie automatically, set by the browser.
    - The policy hash (`13F6CC6C06AE73A6E95DE9B0233AB365`) and service interface name are the
      **same values** seen in `generateAuthorizationToken` below — not a coincidence: GWT's RPC
      policy hash is per-{service, permutation}, not per-method, so every call to
      `CronometerService` under permutation `1008F9FC280FA1C9D6FFD0C3960CC121` shares it. (First
      captured this session; briefly mis-redacted as `<32/43-char>` placeholders in an earlier
      pass of this note — they were never secrets, just non-obviously-safe-looking strings an
      over-cautious redaction script caught by length.)
  - Headers: same as `generateAuthorizationToken` below — `X-GWT-Permutation:
    1008F9FC280FA1C9D6FFD0C3960CC121`, `X-GWT-Module-Base: https://cronometer.com/cronometer/`.
  - **Response starts `//OK[<userId>,...` with userId as the literal first bracketed integer** —
    exactly what plan §1 step 3 guessed. `auth.py` just needs `//OK[(\d+),` as the extraction
    regex; everything after the first comma is dashboard state we don't care about.
- **Dashboard-noise RPC methods observed post-login** (for future reference, so nobody mistakes
  these for the export flow again): `getNutrientInfo`, `setTrackingId`, `getMetrics`,
  `getPublicStripeKey`, `setTimeZone`, `getChartsConfigData`, `getAd`, `getBrazeSDKConfig`,
  `getDrinkingWater`, `flagActiveForUser`, `getCalendarInfo`, `getDayInfo`, `getOffer`,
  `checkMessages`, `areThereRepeatItemsToBeLoggedForDay`, `getDashboardConfig`, `getBrazeEvents`,
  `getCaloriesConsumedAndBurned`, `getBiometrics`, `getSleepBiometricsByDayAndMetricId`,
  `getFood`, `getUserFasts`, `keepAlive`. None of these are needed for this project.

## Confirmed 2026-09-18: export flow, end to end

Captured from a browser already logged in, via Account Data → export (nav path above), 3-day
range, Daily Summary. All values below are templated — real `sesnonce`/token/userId values are
**not** recorded here, per rule 4 of the plan's Phase 0 and mistake #5 in
[[cronometer-execution-brief]].

**Step A — `generateAuthorizationToken` GWT-RPC call** (`POST cronometer.com/cronometer/app`):

Headers: `Content-Type: text/x-gwt-rpc; charset=utf-8`, `X-GWT-Module-Base:
https://cronometer.com/cronometer/`, `X-GWT-Permutation: 1008F9FC280FA1C9D6FFD0C3960CC121`
(→ `config.toml`'s `gwt_permutation`).

Body template (verbatim structure, `%SESNONCE%`/`%USER_ID%` are the only substitution points;
`3600` is a literal TTL in seconds, not user-specific):

```
7|0|8|https://cronometer.com/cronometer/|13F6CC6C06AE73A6E95DE9B0233AB365|com.cronometer.shared.rpc.CronometerService|generateAuthorizationToken|java.lang.String/2004016611|I|com.cronometer.shared.user.AuthScope/2065601159|%SESNONCE%|1|2|3|4|4|5|6|6|7|8|%USER_ID%|3600|7|2|
```

- `13F6CC6C06AE73A6E95DE9B0233AB365` is a policy/content hash, separate from the permutation
  hash in the header — also copy verbatim, also breaks on redeploy.
- The `sesnonce` **cookie value** is passed again as an RPC parameter here — confirms `sesnonce`
  is both a cookie and something the client must read back out and resend, not purely
  cookie-jar-transparent.
- `%USER_ID%` is a plain decimal number (e.g. `14913987`-shaped), sent as a literal integer in
  the payload, not a string-table entry. Not itself secret, but treat as user-specific — don't
  hardcode this specific value as a fixture.
- Response body: **not yet captured redacted** — grab it next time (should carry the export
  token, likely `//OK["<token>"...` shape, analogous to what §1 of the plan guessed).

**Step B — the actual export GET** (matches the plan's §1 step 5 exactly, param name confirmed
`nonce`):

```
GET https://cronometer.com/export?nonce=%TOKEN%&generate=dailySummary&start=%START%&end=%END%
```

`%TOKEN%` comes from Step A's response. `start`/`end` are `YYYY-MM-DD`. Response: raw CSV text,
`Content-Type` not yet noted — capture that alongside the response body next time. Confirmed
one GET, no redirect, no HTML wrapper — the CSV is the whole body.

**Confirmed CSV header row** (column names only — **never** record real data rows here or in
any git-tracked file; this account's export was captured with real intake data that must not be
committed):

```
Date,Energy (kcal),Alcohol (g),Caffeine (mg),Oxalate (mg),Phytate (mg),Water (g),B1 (Thiamine) (mg),B2 (Riboflavin) (mg),B3 (Niacin) (mg),B5 (Pantothenic Acid) (mg),B6 (Pyridoxine) (mg),B12 (Cobalamin) (µg),Folate (µg),Vitamin A (µg),Vitamin C (mg),Vitamin D (IU),Vitamin E (mg),Vitamin K (µg),Calcium (mg),Copper (mg),Iron (mg),Magnesium (mg),Manganese (mg),Phosphorus (mg),Potassium (mg),Selenium (µg),Sodium (mg),Zinc (mg),Net Carbs (g),Carbs (g),Fiber (g),Insoluble Fiber (g),Soluble Fiber (g),Starch (g),Sugars (g),Added Sugars (g),Fat (g),Cholesterol (mg),Monounsaturated (g),Polyunsaturated (g),Saturated (g),Trans-Fats (g),Omega-3 (g),ALA (g),DHA (g),EPA (g),Omega-6 (g),AA (g),LA (g),Cystine (g),Histidine (g),Isoleucine (g),Leucine (g),Lysine (g),Methionine (g),Phenylalanine (g),Protein (g),Threonine (g),Tryptophan (g),Tyrosine (g),Valine (g),Completed
```

- This account's energy unit is **kcal** (header literally says `Energy (kcal)`) — no kJ
  conversion exercised by this fixture; the parser must still handle a kJ header generically per
  the plan, just not tested against a real kJ account yet.
- Required-field columns present exactly as the plan's candidate list expects: `Date`,
  `Energy (kcal)`, `Carbs (g)`, `Fat (g)`, `Protein (g)`.
- `Completed` is a trailing boolean (`true`/`false`) flag column — the plan's "completeness flag."
- ~80 nutrient columns between Date and Completed, matches plan §1.2's expectation.
- Next step for the test fixture (plan Phase 0, item 5): build
  `tests/fixtures/cronometer_daily_summary.csv` using this exact header row with **made-up**
  numbers for 3 rows — do not copy real values in even temporarily.

## Remaining gaps (nice-to-have, not blocking Phase 1)

1. **`generateAuthorizationToken`'s response body**, redacted — we know the resulting token is a
   32-char hex string (seen plain in the `/export?nonce=...` URL it feeds into), but not the
   exact wrapper around it in the RPC response text. Plan §1 guessed `//OK["<token>"...]` with
   quotes. `auth.py`'s extraction regex should just look for the first 32-char hex string in the
   response rather than assume exact bracket/quote placement, which makes this gap safe to skip.
2. Exact `Content-Type` response headers for the `generateAuthorizationToken` and `/export`
   calls — not observed directly, inferred from the `/export` request's `Accept` header
   (`text/html,...`) and the fact the response body was plain CSV text with no HTML wrapper.

Both `a497c077...`/`f795e7d3...`/`d1c8055...`-style hex-named cookies seen in the 2026-09-18
session's request `Cookie` headers are **not** `sesnonce` and not the login-cleared stale
cookie either — likely third-party tracking cookies (Zendesk chat widget, A/B testing). Not
needed for auth; `auth.py` only needs to persist `sesnonce`, `AWSALB`, and `AWSALBCORS`.

The `userCode` question (was: what is it for) is resolved above — send blank.
