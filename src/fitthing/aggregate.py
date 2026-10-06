"""Day-level CSV combining Google Health raw exports and Cronometer nutrition.

Reads the raw per-window JSON files `fitthing export` already writes under
config.raw_dir, aggregates each supported Google data type to one value per
local calendar day, merges in Cronometer's data/nutrition/daily.json, and
writes a single CSV covering a fixed trailing window (config.csv_days).
Every day in that window gets a row even with no data at all, so gaps are
visible and — for weight/body fat — interpolated, and — for the nutrition
columns — filled from config.target_settings_path. See
agent-notes/2026-09-24-google-daily-csv.md and
agent-notes/2026-09-24-combined-daily-csv-gapfill.md for how this was scoped.

Adding a Google-sourced column: write an `_extract_*` function returning
(day, value) from one raw dataPoint, then add an Aggregator entry below.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from fitthing.config import Config
from fitthing.cronometer.export import load_daily_json

DAILY_CSV_PATH = Path("health") / "daily.csv"

# daily.json key -> CSV column, and the matching key in target_settings_path's
# JSON used to fill a day with no logged nutrition at all.
NUTRITION_COLUMNS: dict[str, str] = {
    "calories_kcal": "Energy (kcal)",
    "protein_g": "Protein (g)",
    "carbs_g": "Carbs (g)",
    "fat_g": "Fat (g)",
}
TARGET_SETTINGS_KEYS: dict[str, str] = {
    "calories": "Energy (kcal)",
    "protein": "Protein (g)",
    "carbs": "Carbs (g)",
    "fat": "Fat (g)",
}

# Missing values in these columns are linearly interpolated between the
# nearest known neighbor on each side (a scale/ring reading is assumed to
# change smoothly day to day). No such assumption holds for steps or
# calories burned, so those are left blank when missing.
INTERPOLATED_COLUMNS = ("Weight (lbs)", "Body Fat (%)")

# Formats for columns not listed here (the extra Cronometer nutrients) fall
# back to DEFAULT_FORMAT.
DEFAULT_FORMAT = "float2"
COLUMN_FORMAT: dict[str, str] = {
    "Energy (kcal)": "float2",
    "Protein (g)": "float2",
    "Carbs (g)": "float2",
    "Fat (g)": "float2",
    "Weight (lbs)": "float2",
    "Body Fat (%)": "float2",
    "Steps": "int",
    "Energy Burned (kcal)": "int",
    "Avg Heart Rate (bpm)": "int",
    "Resting Heart Rate (bpm)": "int",
    "SpO2 (%)": "float2",
    "Sleep (hrs)": "float2",
    "Exercise (min)": "int",
}


def _civil_date(civil_time: dict) -> date:
    d = civil_time["date"]
    return date(d["year"], d["month"], d["day"])


def _date_field(d: dict) -> date:
    return date(d["year"], d["month"], d["day"])


def _utc(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00"))


def _local_date(timestamp: str, utc_offset: str) -> date:
    """Local calendar date of an RFC3339 UTC timestamp, given the API's
    offset string (e.g. "-18000s"). Used for types whose intervals carry no
    civil (local) time of their own: sleep and exercise."""
    return (_utc(timestamp) + timedelta(seconds=int(utc_offset.rstrip("s")))).date()


def _package_name(point: dict) -> str | None:
    return point.get("dataSource", {}).get("application", {}).get("packageName")


def _extract_weight(point: dict) -> tuple[date, float]:
    w = point["weight"]
    day = _civil_date(w["sampleTime"]["civilTime"])
    return day, w["weightGrams"] * 0.00220462262185  # grams -> lbs


def _extract_body_fat(point: dict) -> tuple[date, float]:
    bf = point["bodyFat"]
    return _civil_date(bf["sampleTime"]["civilTime"]), bf["percentage"]


def _extract_active_energy_burned(point: dict) -> tuple[date, float]:
    e = point["activeEnergyBurned"]
    return _civil_date(e["interval"]["civilStartTime"]), float(e["kcal"])


def _extract_heart_rate(point: dict) -> tuple[date, float]:
    hr = point["heartRate"]
    return _civil_date(hr["sampleTime"]["civilTime"]), float(hr["beatsPerMinute"])


def _extract_resting_heart_rate(point: dict) -> tuple[date, float]:
    r = point["dailyRestingHeartRate"]
    return _date_field(r["date"]), float(r["beatsPerMinute"])


def _extract_oxygen_saturation(point: dict) -> tuple[date, float]:
    o = point["dailyOxygenSaturation"]
    return _date_field(o["date"]), float(o["averagePercentage"])


def _extract_sleep(point: dict) -> tuple[date, float]:
    """Hours asleep, credited to the local date the session ended (the
    morning you woke up), so one night lands on one row."""
    s = point["sleep"]
    interval = s["interval"]
    day = _local_date(interval["endTime"], interval["endUtcOffset"])
    return day, float(s["summary"]["minutesAsleep"]) / 60


@dataclass(frozen=True)
class Aggregator:
    data_type_id: str  # matches the raw_dir subfolder, e.g. "steps"
    column: str  # CSV header for this column
    agg: str  # "sum" or "mean" across same-day samples
    fmt: str  # "int" or "float2", how to render a value as text
    filtered: bool  # whether csv_device_packages applies to this type
    extract: Callable[[dict], tuple[date, float]]


AGGREGATORS: list[Aggregator] = [
    Aggregator("weight", "Weight (lbs)", "mean", "float2", filtered=False, extract=_extract_weight),
    Aggregator("body-fat", "Body Fat (%)", "mean", "float2", filtered=False, extract=_extract_body_fat),
    Aggregator(
        "active-energy-burned",
        "Energy Burned (kcal)",
        "sum",
        "int",
        filtered=True,
        extract=_extract_active_energy_burned,
    ),
    # Single-source today (the ring), so not device-filtered.
    Aggregator("heart-rate", "Avg Heart Rate (bpm)", "mean", "int", filtered=False, extract=_extract_heart_rate),
    Aggregator(
        "daily-resting-heart-rate",
        "Resting Heart Rate (bpm)",
        "mean",
        "int",
        filtered=False,
        extract=_extract_resting_heart_rate,
    ),
    Aggregator(
        "daily-oxygen-saturation", "SpO2 (%)", "mean", "float2", filtered=False, extract=_extract_oxygen_saturation
    ),
    Aggregator("sleep", "Sleep (hrs)", "sum", "float2", filtered=False, extract=_extract_sleep),
]

# Built from timed intervals rather than per-point values, so they're not
# Aggregators: see _steps_by_day and _exercise_minutes_by_day.
STEPS_COLUMN = "Steps"
EXERCISE_COLUMN = "Exercise (min)"

GOOGLE_COLUMNS: list[str] = [
    "Weight (lbs)",
    "Body Fat (%)",
    STEPS_COLUMN,
    "Energy Burned (kcal)",
    "Avg Heart Rate (bpm)",
    "Resting Heart Rate (bpm)",
    "SpO2 (%)",
    "Sleep (hrs)",
    EXERCISE_COLUMN,
]

# Core nutrition columns first, then the Google-sourced ones — matches the
# order the original hand-built output.csv used. The extra Cronometer
# nutrients (see nutrient_columns) follow these.
CSV_COLUMNS: list[str] = list(NUTRITION_COLUMNS.values()) + GOOGLE_COLUMNS


def _load_window_files(raw_dir: Path, data_type_id: str) -> list[dict]:
    type_dir = raw_dir / data_type_id
    if not type_dir.exists():
        return []
    payloads = []
    for path in sorted(type_dir.glob("*.json")):
        try:
            payloads.append(json.loads(path.read_text()))
        except (ValueError, OSError):
            continue
    return payloads


def _day_owners(payloads: list[dict]) -> dict[date, int]:
    """The freshest payload (by id()) covering each day in its window range.

    Backfills produce non-overlapping windows, but every incremental run
    re-fetches the last STATE_OVERLAP_DAYS days fresh (fitthing.export), so
    a later window file's range can overlap an earlier one's. Whichever
    file was fetched last owns those days, so a day covered by two files is
    never double-counted.
    """
    owners: dict[date, int] = {}
    for payload in sorted(payloads, key=lambda p: p["fetched_at"]):
        start = date.fromisoformat(payload["window_start"])
        end = date.fromisoformat(payload["window_end"])  # exclusive
        day = start
        while day < end:
            owners[day] = id(payload)
            day += timedelta(days=1)
    return owners


def _owned_points(
    raw_dir: Path, data_type_id: str, day_of: Callable[[dict], date]
) -> list[tuple[date, dict]]:
    """(day, point) for every point whose day its window file owns — see
    _day_owners. Points `day_of` can't place are dropped."""
    payloads = _load_window_files(raw_dir, data_type_id)
    owners = _day_owners(payloads)
    result = []
    for payload in payloads:
        for point in payload.get("dataPoints", []):
            try:
                day = day_of(point)
            except (KeyError, TypeError, ValueError):
                continue
            if owners.get(day) == id(payload):
                result.append((day, point))
    return result


def _samples_for(
    raw_dir: Path, aggregator: Aggregator, device_packages: list[str]
) -> dict[date, list[float]]:
    samples: dict[date, list[float]] = {}
    for day, point in _owned_points(raw_dir, aggregator.data_type_id, lambda p: aggregator.extract(p)[0]):
        if aggregator.filtered and device_packages and _package_name(point) not in device_packages:
            continue
        samples.setdefault(day, []).append(aggregator.extract(point)[1])
    return samples


@dataclass(frozen=True)
class _Span:
    """A timed interval: start/end in UTC (None if the point has no
    timestamps), the local day it's credited to, and its value."""

    start: datetime | None
    end: datetime | None
    day: date
    value: float


def _interval_day(interval: dict) -> date:
    if "civilStartTime" in interval:
        return _civil_date(interval["civilStartTime"])
    return _local_date(interval["startTime"], interval["startUtcOffset"])


def _span(day: date, interval: dict, value: float) -> _Span:
    start = _utc(interval["startTime"]) if "startTime" in interval else None
    end = _utc(interval["endTime"]) if "endTime" in interval else None
    return _Span(start, end, day, value)


def _merge_overlapping(spans: list[_Span], combine: Callable[[_Span, _Span], float]) -> list[_Span]:
    """Collapse overlapping spans into one, keeping the earliest start's day."""
    merged: list[_Span] = []
    for span in sorted(spans, key=lambda s: s.start):
        last = merged[-1] if merged else None
        if last is not None and span.start < last.end:
            merged[-1] = _Span(last.start, max(last.end, span.end), last.day, combine(last, span))
        else:
            merged.append(span)
    return merged


def _overlap_seconds(span: _Span, sessions: list[_Span]) -> float:
    return sum(
        max(0.0, (min(span.end, s.end) - max(span.start, s.start)).total_seconds()) for s in sessions
    )


def _fraction_outside(span: _Span, sessions: list[_Span]) -> float:
    """Share of `span` that falls outside every (non-overlapping) session.

    The ring reports steps in 30-minute buckets, so a bucket that's half
    treadmill keeps half its count. A point with no timestamps can't be
    placed, so it's kept whole; an instantaneous one is all-or-nothing.
    """
    if span.start is None or span.end is None or not sessions:
        return 1.0
    total = (span.end - span.start).total_seconds()
    if total <= 0:
        return 0.0 if any(s.start <= span.start < s.end for s in sessions) else 1.0
    return max(0.0, 1.0 - _overlap_seconds(span, sessions) / total)


def _steps_by_day(
    raw_dir: Path, device_packages: list[str], treadmill_packages: list[str]
) -> dict[date, float]:
    """Steps per day: the primary source(s), except while a treadmill runs.

    `device_packages` (the ring) is the primary source. During a treadmill
    session the treadmill's own count replaces whatever the primary source
    logged for that stretch: the ring misses steps when the hand is still
    (e.g. working at a walking desk), while the treadmill counts the belt.
    Overlapping treadmill records are one session uploaded twice (seen
    live: identical counts, start times ~80 min apart), so they're merged
    and counted once. Empty `device_packages` means every non-treadmill
    source. See agent-notes/2026-10-06-treadmill-steps.md.
    """
    points = _owned_points(raw_dir, "steps", lambda p: _civil_date(p["steps"]["interval"]["civilStartTime"]))

    treadmill: list[_Span] = []
    primary: list[_Span] = []
    for day, point in points:
        package = _package_name(point)
        try:
            span = _span(day, point["steps"]["interval"], float(point["steps"]["count"]))
        except (KeyError, TypeError, ValueError):
            continue
        if package in treadmill_packages:
            if span.start is not None and span.end is not None:
                treadmill.append(span)
        elif not device_packages or package in device_packages:
            primary.append(span)

    sessions = _merge_overlapping(treadmill, lambda a, b: max(a.value, b.value))

    totals: dict[date, float] = {}
    for span in primary:
        totals[span.day] = totals.get(span.day, 0.0) + span.value * _fraction_outside(span, sessions)
    for session in sessions:
        totals[session.day] = totals.get(session.day, 0.0) + session.value
    return totals


def _exercise_minutes_by_day(raw_dir: Path) -> dict[date, float]:
    """Wall-clock exercise minutes per day, overlapping sessions counted once.

    The phone and the treadmill app can both log the same walk, so summing
    each session's duration would double-count it.
    """
    points = _owned_points(raw_dir, "exercise", lambda p: _interval_day(p["exercise"]["interval"]))
    spans = []
    for day, point in points:
        try:
            span = _span(day, point["exercise"]["interval"], 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        if span.start is not None and span.end is not None:
            spans.append(span)

    totals: dict[date, float] = {}
    for session in _merge_overlapping(spans, lambda a, b: 0.0):
        minutes = (session.end - session.start).total_seconds() / 60
        totals[session.day] = totals.get(session.day, 0.0) + minutes
    return totals


def build_daily_rows(
    raw_dir: Path,
    device_packages: list[str],
    treadmill_packages: list[str] | None = None,
) -> dict[date, dict[str, float | None]]:
    """One row per calendar day that has at least one value in any column."""
    samples: dict[str, dict[date, list[float]]] = {
        aggregator.column: _samples_for(raw_dir, aggregator, device_packages) for aggregator in AGGREGATORS
    }
    columns: dict[str, dict[date, float]] = {}
    for aggregator in AGGREGATORS:
        per_day = samples[aggregator.column]
        if aggregator.agg == "sum":
            columns[aggregator.column] = {day: sum(v) for day, v in per_day.items()}
        else:
            columns[aggregator.column] = {day: sum(v) / len(v) for day, v in per_day.items()}
    columns[STEPS_COLUMN] = _steps_by_day(raw_dir, device_packages, treadmill_packages or [])
    columns[EXERCISE_COLUMN] = _exercise_minutes_by_day(raw_dir)

    days = sorted({day for per_day in columns.values() for day in per_day})
    rows: dict[date, dict[str, float | None]] = {}
    for day in days:
        rows[day] = {column: columns[column].get(day) for column in GOOGLE_COLUMNS}
    return rows


def _load_nutrition(daily_json_path: Path) -> dict[date, dict]:
    raw = load_daily_json(daily_json_path)
    result = {}
    for iso_date, values in raw.items():
        try:
            result[date.fromisoformat(iso_date)] = values
        except ValueError:
            continue
    return result


def nutrient_columns(nutrition: dict[date, dict]) -> list[str]:
    """Every extra Cronometer nutrient seen in any day, alphabetically.

    Which columns exist depends on the account's Cronometer settings, and
    days merged before nutrients were stored have none, so the set is the
    union across days rather than a fixed list.
    """
    names = {name for values in nutrition.values() for name in values.get("nutrients", {})}
    return sorted(names - set(CSV_COLUMNS), key=str.lower)


def _load_target_settings(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return {}


def _interpolate(values: list[float | None]) -> list[float | None]:
    """Fill each None that has a known value on both sides, linearly.

    A leading or trailing run of Nones has no anchor on one side, so — like
    pandas' default `.interpolate()` — it's left as None rather than
    extrapolated or forward/back-filled.
    """
    result = list(values)
    n = len(result)
    i = 0
    while i < n:
        if result[i] is not None:
            i += 1
            continue
        gap_start = i
        while i < n and result[i] is None:
            i += 1
        gap_end = i  # index of the next known value, or n if none follows
        if gap_start == 0 or gap_end == n:
            continue  # leading/trailing gap: no anchor on one side
        left, right = result[gap_start - 1], result[gap_end]
        span = gap_end - gap_start + 1
        for offset in range(gap_start, gap_end):
            frac = (offset - gap_start + 1) / span
            result[offset] = left + frac * (right - left)
    return result


def build_combined_rows(
    config: Config,
    device_packages: list[str] | None = None,
    end: date | None = None,
) -> dict[date, dict[str, float | None]]:
    """One row for every day in the trailing config.csv_days window.

    Every day gets a row regardless of whether any source has data for it,
    so gaps are visible for interpolation/target-filling below. `end`
    defaults to yesterday (today excluded — a day still in progress isn't a
    complete reading, matching fitthing.export's convention); pass it
    explicitly for deterministic tests.
    """
    end = end if end is not None else date.today() - timedelta(days=1)
    days = [end - timedelta(days=n) for n in range(config.csv_days - 1, -1, -1)]

    packages = device_packages if device_packages is not None else config.csv_device_packages
    google_rows = build_daily_rows(config.raw_dir, packages, config.csv_treadmill_packages)
    nutrition = _load_nutrition(config.output_dir / "nutrition" / "daily.json")
    extra_columns = nutrient_columns(nutrition)
    targets = _load_target_settings(config.target_settings_path)

    rows: dict[date, dict[str, float | None]] = {}
    for day in days:
        row: dict[str, float | None] = {}
        google = google_rows.get(day, {})
        for column in GOOGLE_COLUMNS:
            row[column] = google.get(column)
        n = nutrition.get(day, {})
        for json_key, column in NUTRITION_COLUMNS.items():
            row[column] = n.get(json_key)
        logged_nutrients = n.get("nutrients", {})
        for column in extra_columns:
            row[column] = logged_nutrients.get(column)
        rows[day] = row

    for column in INTERPOLATED_COLUMNS:
        interpolated = _interpolate([rows[day][column] for day in days])
        for day, value in zip(days, interpolated):
            rows[day][column] = value

    for setting_key, column in TARGET_SETTINGS_KEYS.items():
        target_value = targets.get(setting_key)
        if target_value is None:
            continue
        for day in days:
            if rows[day][column] is None:
                rows[day][column] = float(target_value)

    return rows


def _format(column: str, value: float | None) -> str:
    if value is None:
        return ""
    if COLUMN_FORMAT.get(column, DEFAULT_FORMAT) == "int":
        return str(round(value))
    return f"{value:.2f}"


def write_daily_csv(
    config: Config,
    device_packages: list[str] | None = None,
    end: date | None = None,
) -> tuple[Path, int]:
    """Rebuild the day-level CSV from whatever raw/nutrition data is on disk.

    Always a full rebuild, not an incremental merge: raw window files and
    data/nutrition/daily.json can each change independently of this call
    (new export runs, --full, manual edits), so recomputing from scratch is
    the only way to stay correct. Returns (path, row count).
    """
    rows = build_combined_rows(config, device_packages=device_packages, end=end)
    any_row = next(iter(rows.values()), {})
    columns = CSV_COLUMNS + [c for c in any_row if c not in CSV_COLUMNS]

    path = config.output_dir / DAILY_CSV_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    with tmp_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Date"] + columns)
        for day in sorted(rows):
            row = rows[day]
            writer.writerow([day.isoformat()] + [_format(column, row[column]) for column in columns])
    os.replace(tmp_path, path)

    return path, len(rows)
