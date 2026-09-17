"""Export orchestration: fetch windows, write JSON, track incremental sync state.

See Phase 4 of agent-notes/2026-09-16-google-health-export-plan.md.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fitthing.client import ApiError, HealthClient
from fitthing.config import Config
from fitthing.datatypes import DataType
from fitthing.windows import build_filter, iter_windows

# Devices/apps can sync a few days late, so every incremental run re-checks
# this many days before where the last run left off.
STATE_OVERLAP_DAYS = 3
DEFAULT_BACKFILL_DAYS = 365


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text())
    except (ValueError, OSError):
        return {}


def save_state(state_path: Path, state: dict) -> None:
    """Write state atomically so a crash mid-write never corrupts it."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_name(state_path.name + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp_path, state_path)


def split_by_granted_scope(
    data_types: list[DataType], granted_scopes: set[str]
) -> tuple[list[DataType], list[DataType]]:
    granted = [dt for dt in data_types if dt.scope in granted_scopes]
    ungranted = [dt for dt in data_types if dt.scope not in granted_scopes]
    return granted, ungranted


def range_for(
    data_type: DataType,
    config: Config,
    state: dict,
    since: date | None,
    until: date | None,
    full: bool,
) -> tuple[date, date]:
    """Compute the [start, end) window for one data type's export.

    `until` is inclusive (the last day to include), matching --until's CLI
    meaning, so the actual exclusive end is `until + 1 day`.
    """
    until_inclusive = until if until is not None else _today_utc()
    end = until_inclusive + timedelta(days=1)

    if since is not None:
        return since, end

    type_state = state.get(data_type.id)
    if full or not type_state or not type_state.get("synced_through"):
        # No prior state (or --full): backfill from config.start_date, or
        # DEFAULT_BACKFILL_DAYS before the end of this run's range.
        if config.start_date:
            start = date.fromisoformat(config.start_date)
        else:
            start = until_inclusive - timedelta(days=DEFAULT_BACKFILL_DAYS)
        return start, end

    synced_through = date.fromisoformat(type_state["synced_through"])
    start = synced_through - timedelta(days=STATE_OVERLAP_DAYS)
    return start, end


def _window_path(raw_dir: Path, data_type_id: str, window_start: date, window_end: date) -> Path:
    return raw_dir / data_type_id / f"{window_start.isoformat()}_{window_end.isoformat()}.json"


def _write_window(
    path: Path,
    data_type_id: str,
    window_start: date,
    window_end: date,
    filter_str: str,
    points: list[dict],
) -> None:
    """Write one window's data atomically (write to .tmp, then rename)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "data_type": data_type_id,
        "window_start": window_start.isoformat(),
        "window_end": window_end.isoformat(),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "filter": filter_str,
        "count": len(points),
        "dataPoints": points,
    }
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def export_data_type(
    client: HealthClient,
    config: Config,
    data_type: DataType,
    state: dict,
    since: date | None,
    until: date | None,
    full: bool,
) -> bool:
    """Export one data type's windows, saving state after each window.

    Returns True on success, False if this type was skipped due to a 403
    (scope revoked/not actually granted). Any other ApiError (e.g. retries
    exhausted under sustained rate limiting) propagates to the caller —
    run_export() catches it there and isolates the failure to this one
    type instead of aborting the whole run. Either way, state up to the
    last completed window has already been saved by the time an error
    surfaces.
    """
    start, end = range_for(data_type, config, state, since, until, full)

    for window_start, window_end in iter_windows(start, end, config.window_days):
        filter_str = build_filter(data_type, window_start, window_end)
        try:
            points = list(client.list_data_points(data_type.id, filter_str, data_type.page_size))
        except ApiError as exc:
            if exc.status_code == 403:
                print(
                    f"{data_type.id}: 403 Forbidden, skipping this type. {exc.body}",
                    file=sys.stderr,
                )
                return False
            raise

        path = _window_path(config.raw_dir, data_type.id, window_start, window_end)
        _write_window(path, data_type.id, window_start, window_end, filter_str, points)
        print(
            f"{data_type.id} {window_start.isoformat()}→{window_end.isoformat()}: "
            f"{len(points)} points",
            file=sys.stderr,
        )

        state.setdefault(data_type.id, {})["synced_through"] = window_end.isoformat()
        state[data_type.id]["last_run"] = datetime.now(timezone.utc).isoformat()
        save_state(config.state_path, state)

    return True


@dataclass
class ExportResult:
    exported: list[str] = field(default_factory=list)
    skipped_ungranted: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def run_export(
    client: HealthClient,
    config: Config,
    data_types: list[DataType],
    granted_scopes: set[str],
    since: date | None = None,
    until: date | None = None,
    full: bool = False,
) -> ExportResult:
    state = load_state(config.state_path)
    granted, ungranted = split_by_granted_scope(data_types, granted_scopes)

    for dt in ungranted:
        print(f"Skipping {dt.id}: scope not granted ({dt.scope}).", file=sys.stderr)

    result = ExportResult(skipped_ungranted=[dt.id for dt in ungranted])
    for dt in granted:
        try:
            ok = export_data_type(client, config, dt, state, since, until, full)
        except ApiError as exc:
            # A real full backfill showed this matters: one type running out
            # of retries (e.g. under sustained rate limiting) used to abort
            # the whole run, so every type after it in the list never even
            # started. Isolate the failure to this type instead — state is
            # already saved up to its last completed window, so a rerun
            # resumes it correctly — and keep going with the rest.
            print(f"{dt.id}: stopped after repeated errors, skipping. {exc}", file=sys.stderr)
            ok = False
        (result.exported if ok else result.failed).append(dt.id)

    return result
