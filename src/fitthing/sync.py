"""Run both pulls — Cronometer, then Google Health — and rebuild daily.csv.

Cronometer goes first so the CSV that `run_export` rebuilds at the end of the
Google pull already includes the freshly merged nutrition. Each source is
isolated: a failure in one (bad credentials, network, API errors) is
recorded and the other still runs, and the CSV is always rebuilt from
whatever is on disk. See agent-notes/2026-10-06-sync-command.md.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import requests

from fitthing.aggregate import write_daily_csv
from fitthing.auth import ALL_SCOPES, ensure_signed_in
from fitthing.client import ApiError, HealthClient
from fitthing.config import Config
from fitthing.cronometer.auth import CronometerAuthError, resolve_credentials
from fitthing.cronometer.client import CronometerClient, CronometerClientError, ExportCapReached
from fitthing.cronometer.export import CronometerExportResult, run_cronometer_export
from fitthing.datatypes import resolve
from fitthing.export import ExportResult, run_export


@dataclass
class SyncResult:
    cronometer: CronometerExportResult | None = None
    cronometer_error: str | None = None
    google: ExportResult | None = None
    google_error: str | None = None
    csv_path: Path | None = None
    csv_days: int = 0

    @property
    def ok(self) -> bool:
        google_ok = self.google is not None and not self.google.failed
        return self.cronometer is not None and google_ok


def _pull_cronometer(config: Config, interactive: bool, full: bool, days: int | None) -> CronometerExportResult:
    creds = resolve_credentials(config.cronometer, interactive=interactive)
    client = CronometerClient(config.cronometer, creds)
    return run_cronometer_export(client, config, full=full, days=days)


def _pull_google(config: Config, interactive: bool, full: bool, days: int | None) -> ExportResult:
    creds = ensure_signed_in(config, ALL_SCOPES, interactive=interactive)
    client = HealthClient(creds, request_delay_seconds=config.request_delay_seconds)
    return run_export(
        client,
        config,
        resolve(config.data_types),
        set(creds.scopes or []),
        full=full,
        days=days,
    )


def _describe(exc: BaseException) -> str:
    # The sign-in helpers sys.exit(2) when credentials are missing and
    # prompting isn't allowed; they've already printed why.
    if isinstance(exc, SystemExit):
        return "sign-in required (see message above)"
    return str(exc) or type(exc).__name__


def run_sync(
    config: Config,
    *,
    interactive: bool,
    full: bool = False,
    days: int | None = None,
) -> SyncResult:
    """Pull Cronometer, then Google Health, then rebuild daily.csv.

    `full` and `days` are passed to both pulls with the same meaning as on
    `cronometer-export` and `export`.
    """
    result = SyncResult()

    try:
        result.cronometer = _pull_cronometer(config, interactive, full, days)
    except (
        CronometerAuthError,
        CronometerClientError,
        ExportCapReached,
        requests.RequestException,
        SystemExit,
    ) as exc:
        result.cronometer_error = _describe(exc)
        print(f"Cronometer pull failed: {result.cronometer_error}", file=sys.stderr)

    try:
        result.google = _pull_google(config, interactive, full, days)
    except (ApiError, ValueError, requests.RequestException, SystemExit) as exc:
        result.google_error = _describe(exc)
        print(f"Google Health pull failed: {result.google_error}", file=sys.stderr)

    if result.google is not None:
        result.csv_path, result.csv_days = result.google.csv_path, result.google.csv_days
    else:
        # run_export didn't get far enough to rebuild the CSV; do it here so
        # new Cronometer data still lands in it.
        result.csv_path, result.csv_days = write_daily_csv(config)

    return result
