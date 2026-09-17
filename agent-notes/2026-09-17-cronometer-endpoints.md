# Finding: Cronometer login flow — Phase 0 recon (IN PROGRESS)

**Date:** 2026-09-17 · **Author:** Claude (Sonnet 5), with the user driving Firefox DevTools · **Status:** PARTIAL — paused mid-capture, resume instructions below.

This is Phase 0 of [2026-09-17-cronometer-nutrition-plan.md](2026-09-17-cronometer-nutrition-plan.md).
Its §1 assumptions (based on `jrmycanady/gocronometer`) are **confirmed stale in several
places** — see below. Do not code against §1 as written; use this note instead, and finish
capturing what's still missing before writing `cronometer/auth.py`.

## Confirmed so far (live, 2026-09-17)

- **Login is still a plain form POST** to `cronometer.com/login`. Response: **200, HTML body**
  (not a redirect, not JSON).
- **Request payload fields:** `username`, `password`, `userCode`, `anticsrf`.
  - `userCode` is **new** — not present in the reference implementation. Purpose unconfirmed
    (guesses: 2FA/remember-device code, affiliate/referral code — do not assume, ask the user
    or send it empty and see what happens when Phase 0 resumes).
  - `anticsrf` still exists, presumably still scraped from the login page HTML as the plan
    assumed — **not yet reconfirmed live**, assume unchanged for now.
- **No cookie named `sesnonce`.** The three `Set-Cookie` headers on the login response are:
  - `AWSALB`, `AWSALBCORS` — AWS load-balancer stickiness cookies. **Not session-related, ignore.**
  - One cookie with an **opaque, apparently-generated hex-like name** (looked like a 32-char hex
    string), `Max-Age=172800` (48h), `Path=/`, `Secure`, `HttpOnly`. **This is almost certainly
    the real session cookie**, replacing `sesnonce` — but its name is not fixed/predictable
    (may be per-deployment or per-session-store config), so **auth.py must read whichever
    non-AWSALB* cookie the login response actually sets, by process of elimination, not by a
    hardcoded name.**
- **GWT is still in use.** A `*.nocache.js` GWT bootstrap file loads after login, and POST
  requests to `cronometer.com/cronometer/app` do fire — but **asynchronously, a few seconds
  after the login page redirect**, mixed in with many other RPC calls for dashboard widgets.
  Trying to identify the auth-token-generation call from that noise is not practical.

## Not yet captured — resume here

The plan (Phase 0, item 2 in the executor rules) needs the **request/response pair(s) that are
specifically part of the export flow**, isolated from dashboard noise. Next session, resume with
the browser already logged in (or log in fresh) and:

1. Open DevTools Network tab, **clear the log**, keep Persist Logs on.
2. Navigate to **Profile → gear icon → Export Data**.
3. Set a short range (e.g. 3 days), pick the **Daily Summary** export type.
4. Click Export/Download and capture everything that fires between the click and the file
   landing, specifically:
   - Any **POST to `cronometer.com/cronometer/app`** in that sequence — full request body,
     full response body, and the `x-gwt-permutation` / `x-gwt-module-base` request headers.
   - The final **GET request that returns the CSV** — full URL including query string (confirm
     whether the param is still named `nonce`, and whether `generate=dailySummary` is still the
     right value — the export type in the UI may be labeled differently now, e.g. just "Daily
     Summary").
5. Also still needed, low priority (can reconfirm anytime): the CSV's real header row (energy
   unit, exact column names) — not yet exported even once this session.
6. Redact actual cookie/nonce/token **values** before recording here; keep field/header names
   and structure.

## Open question to resolve when resuming

**What is `userCode` for, and what should `fitthing` send in it?** Ask the user directly — do
not guess a default. If it turns out to be optional/blank-safe, note that here.
