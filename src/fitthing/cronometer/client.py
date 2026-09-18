"""HTTP client for Cronometer's unofficial Daily Summary export endpoint.

Mirrors fitthing.client's retry/backoff policy (copied, not shared — see
agent-notes/2026-09-17-cronometer-nutrition-plan.md §2 on why these two
sources don't share an abstraction). Session reuse and the one-retry-then-fail
re-login contract are per Phase 2 of that plan; the export cap and expired-
session detection are Phase 3.
"""

from __future__ import annotations

import random
import time
from datetime import date

import requests

from fitthing.config import CronometerConfig
from fitthing.cronometer.auth import (
    CronometerAuthError,
    CronometerCredentials,
    get_export_token,
    load_cached_session,
    sign_in,
)

EXPORT_URL = "https://cronometer.com/export"

RETRY_STATUSES = {429, 500, 502, 503, 504}
BACKOFF_SCHEDULE_SECONDS = [1, 2, 4, 8, 16]
MAX_RETRIES = len(BACKOFF_SCHEDULE_SECONDS)
REQUEST_TIMEOUT_SECONDS = 60

# A CSV export never contains this literal string; Cronometer's own login
# page always does (see cronometer/auth.py's anticsrf scraping). An expired
# or missing session comes back as this HTML page in place of the CSV.
_LOGIN_PAGE_MARKER = 'name="anticsrf"'


class ExportCapReached(Exception):
    """Cronometer's ~10/day personal export cap has been hit. Never retried."""


class CronometerClientError(Exception):
    """A non-retryable HTTP error from the export endpoint."""


class _SessionExpired(Exception):
    """Internal signal: retry once with a freshly logged-in session."""


def _looks_like_cap_reached(text: str) -> bool:
    lowered = text.lower()
    return "export limit" in lowered or "too many exports" in lowered


class CronometerClient:
    """Fetches the Daily Summary CSV for a date range. One request per call."""

    def __init__(
        self,
        config: CronometerConfig,
        credentials: CronometerCredentials,
        session: requests.Session | None = None,
        sleep=time.sleep,
    ):
        self._config = config
        self._credentials = credentials
        self._session = session or load_cached_session(config.session_path)
        self._sleep = sleep

    def export_daily_summary(self, start: date, end: date) -> str:
        """Return the raw Daily Summary CSV text for [start, end].

        Tries the cached session first. If it's stale (the authenticate/token
        RPCs fail, or the export itself comes back as an HTML login page),
        logs in fresh exactly once and retries; a second failure propagates.
        """
        session = self._session or self._login_fresh()
        try:
            return self._export_once(session, start, end)
        except (_SessionExpired, CronometerAuthError):
            session = self._login_fresh()
            return self._export_once(session, start, end)

    def _login_fresh(self) -> requests.Session:
        self._session = sign_in(self._config, self._credentials)
        return self._session

    def _export_once(self, session: requests.Session, start: date, end: date) -> str:
        token = get_export_token(session, self._config)
        params = {
            "nonce": token,
            "generate": "dailySummary",
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        for attempt in range(MAX_RETRIES + 1):
            if self._config.request_delay_seconds:
                self._sleep(self._config.request_delay_seconds)
            resp = session.get(EXPORT_URL, params=params, timeout=REQUEST_TIMEOUT_SECONDS)
            if resp.status_code in RETRY_STATUSES:
                if attempt == MAX_RETRIES:
                    raise CronometerClientError(
                        f"Cronometer export failed with HTTP {resp.status_code} after retries."
                    )
                self._sleep(self._retry_delay(resp, attempt))
                continue
            if resp.status_code >= 400:
                raise CronometerClientError(
                    f"Cronometer export HTTP {resp.status_code}: {resp.text[:200]}"
                )
            if _looks_like_cap_reached(resp.text):
                raise ExportCapReached(
                    "Cronometer's daily export limit (~10/day) is reached; try again tomorrow."
                )
            if _LOGIN_PAGE_MARKER in resp.text:
                raise _SessionExpired()
            return resp.text
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _retry_delay(resp, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return float(retry_after)
            except ValueError:
                pass
        base = BACKOFF_SCHEDULE_SECONDS[attempt]
        return base + random.uniform(0, base * 0.1)
