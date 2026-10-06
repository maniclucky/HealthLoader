"""Cronometer export orchestration: one request per run, merge into daily.json.

See Phase 4 of agent-notes/2026-09-17-cronometer-nutrition-plan.md. Deliberately
does not import from fitthing.export/windows — those are the Google path's
half-open, windowed, per-data-type state; this path is a single request over
an inclusive [start, end] range, keyed by one fixed state id. Small patterns
(atomic tmp+os.replace writes, a state.json dict) are copied, not shared.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from fitthing.config import Config, CronometerConfig
from fitthing.cronometer.client import CronometerClient
from fitthing.cronometer.parse import DailyNutrition, parse_daily_summary

# Matches fitthing.export.STATE_OVERLAP_DAYS's rationale: entries can be
# logged/edited a few days late, so every incremental run re-checks this
# many days before where the last run left off.
STATE_OVERLAP_DAYS = 3
DEFAULT_BACKFILL_DAYS = 30
MAX_BACKFILL_DAYS = 365
STATE_KEY = "cronometer-daily-summary"


def load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {}
    try:
        return json.loads(state_path.read_text())
    except (ValueError, OSError):
        return {}


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = state_path.with_name(state_path.name + ".tmp")
    tmp_path.write_text(json.dumps(state, indent=2, sort_keys=True))
    os.replace(tmp_path, state_path)


def load_daily_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return {}


def save_daily_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)


def merge_daily_nutrition(existing: dict, rows: list[DailyNutrition]) -> dict:
    """Overwrite any date `rows` contains; every other date is left untouched."""
    merged = dict(existing)
    for row in rows:
        merged[row.date] = {
            "calories_kcal": row.calories_kcal,
            "protein_g": row.protein_g,
            "carbs_g": row.carbs_g,
            "fat_g": row.fat_g,
            "nutrients": row.nutrients,
        }
    return merged


def range_for(
    cronometer_config: CronometerConfig,
    state: dict,
    since: date | None,
    until: date | None,
    full: bool,
    days: int | None = None,
) -> tuple[date, date]:
    """The inclusive [start, end] range for one export run — never windowed.

    Cronometer's export cap (~10/day) makes the Google path's 14-day-window
    backfill unworkable here; the endpoint accepts an arbitrary range, so a
    full year's backfill is one request, not ~26. `until` defaults to the
    *local* calendar day — Cronometer's own days are local, not UTC (see
    agent-notes/2026-09-17-cronometer-nutrition-plan.md §1.3).

    Today (local) is always excluded, even if explicitly requested via
    --until: a day still in progress is inherently an incomplete data set,
    not a real reading.

    `days` overrides DEFAULT_BACKFILL_DAYS for a fresh backfill (no prior
    state, no --since); it's ignored once cronometer_config.start_date is
    set, since that's a more specific override.
    """
    until_date = until if until is not None else date.today()
    until_date = min(until_date, date.today() - timedelta(days=1))

    if since is not None:
        return since, until_date

    type_state = state.get(STATE_KEY)
    if full or not type_state or not type_state.get("synced_through"):
        if cronometer_config.start_date:
            start = date.fromisoformat(cronometer_config.start_date)
        else:
            backfill_days = days if days is not None else DEFAULT_BACKFILL_DAYS
            start = until_date - timedelta(days=backfill_days)
        return start, until_date

    synced_through = date.fromisoformat(type_state["synced_through"])
    start = synced_through - timedelta(days=STATE_OVERLAP_DAYS)
    return start, until_date


@dataclass
class CronometerExportResult:
    days_updated: int
    range_start: date
    range_end: date


def run_cronometer_export(
    client: CronometerClient,
    config: Config,
    since: date | None = None,
    until: date | None = None,
    full: bool = False,
    days: int | None = None,
) -> CronometerExportResult:
    """Fetch, save the raw CSV, merge into daily.json, and advance state.

    State is only saved at the very end: a crash after the download (raw
    CSV written, or even mid-merge) but before this point leaves state
    unadvanced, so a rerun recomputes the same range and retries — the
    merge is idempotent, so that's safe.
    """
    state = load_state(config.state_path)
    start, end = range_for(config.cronometer, state, since, until, full, days)

    csv_text = client.export_daily_summary(start, end)

    raw_path = config.raw_dir / "cronometer-daily-summary" / f"{start.isoformat()}_{end.isoformat()}.csv"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = raw_path.with_name(raw_path.name + ".tmp")
    tmp_path.write_text(csv_text, encoding="utf-8", newline="")
    os.replace(tmp_path, raw_path)

    rows = parse_daily_summary(csv_text, start=start, end=end)

    daily_json_path = config.output_dir / "nutrition" / "daily.json"
    existing = load_daily_json(daily_json_path)
    merged = merge_daily_nutrition(existing, rows)
    save_daily_json(daily_json_path, merged)

    state[STATE_KEY] = {
        "synced_through": end.isoformat(),
        "last_run": datetime.now(timezone.utc).isoformat(),
    }
    save_state(config.state_path, state)

    return CronometerExportResult(days_updated=len(rows), range_start=start, range_end=end)


def run_cronometer_import(config: Config, csv_path: Path) -> int:
    """The manual fallback: parse + merge a CSV the user exported by hand.

    Returns the number of days imported. Raises ValueError (naming every
    header actually seen) if the CSV's columns can't be matched — same
    contract as run_cronometer_export, left for the caller to print.
    """
    csv_text = csv_path.read_text(encoding="utf-8")
    rows = parse_daily_summary(csv_text)

    daily_json_path = config.output_dir / "nutrition" / "daily.json"
    existing = load_daily_json(daily_json_path)
    merged = merge_daily_nutrition(existing, rows)
    save_daily_json(daily_json_path, merged)

    return len(rows)
