"""HTTP client for the Google Health API: pagination + retry/backoff.

See Phase 3 of agent-notes/2026-09-16-google-health-export-plan.md.
"""

from __future__ import annotations

import random
import time

from google.auth.transport.requests import AuthorizedSession

BASE_URL = "https://health.googleapis.com/v4"

RETRY_STATUSES = {429, 500, 502, 503, 504}

# Exponential backoff before each of up to 5 retries (6 attempts total),
# honoring Retry-After when the server sends one.
BACKOFF_SCHEDULE_SECONDS = [1, 2, 4, 8, 16]
MAX_RETRIES = len(BACKOFF_SCHEDULE_SECONDS)
REQUEST_TIMEOUT_SECONDS = 60


class ApiError(Exception):
    """A non-retryable API error (4xx other than 429, or a retry budget used up)."""

    def __init__(self, status_code: int, body: str):
        super().__init__(f"Google Health API error {status_code}: {body}")
        self.status_code = status_code
        self.body = body


class HealthClient:
    """Thin wrapper over an AuthorizedSession for the Google Health API.

    AuthorizedSession attaches the bearer token and refreshes it on 401
    automatically, so this class only has to handle pagination and the
    429/5xx retry policy from the plan.
    """

    def __init__(self, credentials, session=None, sleep=time.sleep):
        self._session = session or AuthorizedSession(credentials)
        self._sleep = sleep

    def _request(self, method: str, url: str, **kwargs):
        for attempt in range(MAX_RETRIES + 1):
            resp = self._session.request(method, url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
            if resp.status_code not in RETRY_STATUSES:
                if resp.status_code >= 400:
                    raise ApiError(resp.status_code, resp.text)
                return resp
            if attempt == MAX_RETRIES:
                raise ApiError(resp.status_code, resp.text)
            self._sleep(self._retry_delay(resp, attempt))
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

    def list_data_points(self, data_type_id: str, filter_str: str, page_size: int):
        """Yield every data point across all pages matching one query."""
        url = f"{BASE_URL}/users/me/dataTypes/{data_type_id}/dataPoints"
        base_params = {"pageSize": page_size}
        if filter_str:
            base_params["filter"] = filter_str

        page_token = None
        while True:
            params = dict(base_params)
            if page_token:
                params["pageToken"] = page_token
            resp = self._request("GET", url, params=params)
            data = resp.json()
            yield from data.get("dataPoints", [])
            page_token = data.get("nextPageToken")
            if not page_token:
                break
