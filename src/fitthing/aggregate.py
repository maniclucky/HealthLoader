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
    "Distance (mi)": "float2",
    "Avg Heart Rate (bpm)": "int",
    "Resting Heart Rate (bpm)": "int",
    "SpO2 (%)": "float2",
    "Sleep (hrs)": "float2",
    "Exercise (min)": "int",
}

MM_PER_MILE = 1_609_344


def _civil_date(civil_time: dict) -> date:
    d = civil_time["date"]
    return date(d["year"], d["month"], d["day"])


def _date_field(d: dict) -> date:
    return date(d["year"], d["month"], d["day"])


def _local_date(timestamp: str, utc_offset: str) -> date:
    """Local calendar date of an RFC3339 UTC timestamp, given the API's
    offset string (e.g. "-18000s"). Used for types whose intervals carry no
    civil (local) time of their own: sleep and exercise."""
    utc = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return (utc + timedelta(seconds=int(utc_offset.rstrip("s")))).date()


def _duration_seconds(duration: str) -> float:
    return float(duration.rstrip("s"))


def _package_name(point: dict) -> str | None:
    return point.get("dataSource", {}).get("application", {}).get("packageName")


def _extract_weight(point: dict) -> tuple[date, float]:
    w = point["weight"]
    day = _civil_date(w["sampleTime"]["civilTime"])
    return day, w["weightGrams"] * 0.00220462262185  # grams -> lbs


def _extract_body_fat(point: dict) -> tuple[date, float]:
    bf = point["bodyFat"]
    return _civil_date(bf["sampleTime"]["civilTime"]), bf["percentage"]


def _extract_steps(point: dict) -> tuple[date, float]:
    s = point["steps"]
    return _civil_date(s["interval"]["civilStartTime"]), float(s["count"])


def _extract_active_energy_burned(point: dict) -> tuple[date, float]:
    e = point["activeEnergyBurned"]
    return _civil_date(e["interval"]["civilStartTime"]), float(e["kcal"])


def _extract_distance(point: dict) -> tuple[date, float]:
    d = point["distance"]
    return _civil_date(d["interval"]["civilStartTime"]), float(d["millimeters"]) / MM_PER_MILE


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


def _extract_exercise(point: dict) -> tuple[date, float]:
    e = point["exercise"]
    interval = e["interval"]
    day = _local_date(interval["startTime"], interval["startUtcOffset"])
    if "activeDuration" in e:
        seconds = _duration_seconds(e["activeDuration"])
    else:
        start = datetime.fromisoformat(interval["startTime"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(interval["endTime"].replace("Z", "+00:00"))
        seconds = (end - start).total_seconds()
    return day, seconds / 60


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
    Aggregator("steps", "Steps", "sum", "int", filtered=True, extract=_extract_steps),
    Aggregator(
        "active-energy-burned",
        "Energy Burned (kcal)",
        "sum",
        "int",
        filtered=True,
        extract=_extract_active_energy_burned,
    ),
    # Not device-filtered: distance comes from the phone and treadmill apps,
    # not the ring, and the rest have a single source today. See
    # agent-notes/2026-10-06-sync-command.md.
    Aggregator("distance", "Distance (mi)", "sum", "float2", filtered=False, extract=_extract_distance),
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
    Aggregator("exercise", "Exercise (min)", "sum", "int", filtered=False, extract=_extract_exercise),
]

# Core nutrition columns first, then the Google-sourced ones — matches the
# order the original hand-built output.csv used. The extra Cronometer
# nutrients (see nutrient_columns) follow these.
CSV_COLUMNS: list[str] = list(NUTRITION_COLUMNS.values()) + [a.column for a in AGGREGATORS]


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


def _samples_for(
    raw_dir: Path, aggregator: Aggregator, device_packages: list[str]
) -> dict[date, list[float]]:
    payloads = _load_window_files(raw_dir, aggregator.data_type_id)
    owners = _day_owners(payloads)

    samples: dict[date, list[float]] = {}
    for payload in payloads:
        for point in payload.get("dataPoints", []):
            if aggregator.filtered and device_packages and _package_name(point) not in device_packages:
                continue
            try:
                day, value = aggregator.extract(point)
            except (KeyError, TypeError, ValueError):
                continue
            if owners.get(day) != id(payload):
                continue  # a fresher file owns this day; skip to avoid double-counting
            samples.setdefault(day, []).append(value)
    return samples


def build_daily_rows(raw_dir: Path, device_packages: list[str]) -> dict[date, dict[str, float | None]]:
    """One row per calendar day that has at least one value in any column."""
    columns: dict[str, dict[date, list[float]]] = {
        aggregator.column: _samples_for(raw_dir, aggregator, device_packages) for aggregator in AGGREGATORS
    }

    days = sorted({day for per_day in columns.values() for day in per_day})
    rows: dict[date, dict[str, float | None]] = {}
    for day in days:
        row: dict[str, float | None] = {}
        for aggregator in AGGREGATORS:
            values = columns[aggregator.column].get(day)
            if not values:
                row[aggregator.column] = None
            elif aggregator.agg == "sum":
                row[aggregator.column] = sum(values)
            else:
                row[aggregator.column] = sum(values) / len(values)
        rows[day] = row
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
    google_rows = build_daily_rows(config.raw_dir, packages)
    nutrition = _load_nutrition(config.output_dir / "nutrition" / "daily.json")
    extra_columns = nutrient_columns(nutrition)
    targets = _load_target_settings(config.target_settings_path)

    rows: dict[date, dict[str, float | None]] = {}
    for day in days:
        row: dict[str, float | None] = {}
        google = google_rows.get(day, {})
        for aggregator in AGGREGATORS:
            row[aggregator.column] = google.get(aggregator.column)
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
