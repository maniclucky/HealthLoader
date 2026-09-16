"""Registry of exportable Google Health API data types.

Built from the table verified live in Phase 2 — see
agent-notes/2026-09-16-account-not-linked-finding.md for how each filter
field and the food/food-measurement-unit exclusion were confirmed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from fitthing.auth import SCOPE_PREFIX


class Kind(Enum):
    """The shape of a data type's timestamps, which determines its filter."""

    INTERVAL = "interval"
    SAMPLE = "sample"
    DAILY = "daily"
    SESSION_CIVIL = "session_civil"
    SESSION_SLEEP = "session_sleep"  # sleep alone: filters on interval.end_time
    SESSION_ECG = "session_ecg"  # electrocardiogram alone: start_time only


class TimeFormat(Enum):
    """How a window boundary is written into a filter string."""

    RFC3339 = "rfc3339"  # e.g. "2026-01-01T00:00:00Z"
    CIVIL = "civil"  # e.g. "2026-01-01T00:00:00" (no zone)
    DATE = "date"  # e.g. "2026-01-01"


_KIND_TIME_FORMAT = {
    Kind.INTERVAL: TimeFormat.RFC3339,
    Kind.SAMPLE: TimeFormat.RFC3339,
    Kind.DAILY: TimeFormat.DATE,
    Kind.SESSION_CIVIL: TimeFormat.CIVIL,
    Kind.SESSION_SLEEP: TimeFormat.RFC3339,
    Kind.SESSION_ECG: TimeFormat.RFC3339,
}

# Fixed page sizes per the plan: sleep and exercise cap at 25; everything
# else can use the API's overall max of 10,000.
_SMALL_PAGE_SIZE_IDS = {"sleep", "exercise"}
DEFAULT_PAGE_SIZE = 10_000
SMALL_PAGE_SIZE = 25


@dataclass(frozen=True)
class DataType:
    id: str  # kebab-case, as used in the URL path, e.g. "heart-rate"
    kind: Kind
    scope: str  # full OAuth scope URL required to read this type
    filter_field: str  # e.g. "steps.interval.start_time"
    time_format: TimeFormat
    page_size: int

    @property
    def snake(self) -> str:
        return self.id.replace("-", "_")


def _filter_field(data_id: str, kind: Kind) -> str:
    snake = data_id.replace("-", "_")
    if kind is Kind.INTERVAL:
        return f"{snake}.interval.start_time"
    if kind is Kind.SAMPLE:
        return f"{snake}.sample_time.physical_time"
    if kind is Kind.DAILY:
        return f"{snake}.date"
    if kind is Kind.SESSION_CIVIL:
        return f"{snake}.interval.civil_start_time"
    if kind is Kind.SESSION_SLEEP:
        return "sleep.interval.end_time"
    if kind is Kind.SESSION_ECG:
        return "electrocardiogram.interval.start_time"
    raise ValueError(f"unhandled kind: {kind}")  # pragma: no cover


def _make(data_id: str, kind: Kind, scope_suffix: str) -> DataType:
    return DataType(
        id=data_id,
        kind=kind,
        scope=f"{SCOPE_PREFIX}{scope_suffix}.readonly",
        filter_field=_filter_field(data_id, kind),
        time_format=_KIND_TIME_FORMAT[kind],
        page_size=SMALL_PAGE_SIZE if data_id in _SMALL_PAGE_SIZE_IDS else DEFAULT_PAGE_SIZE,
    )


# (data type id, kind, scope suffix) — see plan §1 for the source table.
# Excluded on purpose: floors, total-calories, calories-in-heart-rate-zone
# (list unsupported per docs); menstrual-period, ovulation-test, moods,
# symptoms (write-only); food, food-measurement-unit (verified in Phase 2
# to be global reference catalogs, not per-user logs).
_ENTRIES: list[tuple[str, Kind, str]] = [
    # activity_and_fitness
    ("steps", Kind.INTERVAL, "activity_and_fitness"),
    ("distance", Kind.INTERVAL, "activity_and_fitness"),
    ("altitude", Kind.INTERVAL, "activity_and_fitness"),
    ("active-energy-burned", Kind.INTERVAL, "activity_and_fitness"),
    ("active-minutes", Kind.INTERVAL, "activity_and_fitness"),
    ("active-zone-minutes", Kind.INTERVAL, "activity_and_fitness"),
    ("activity-level", Kind.INTERVAL, "activity_and_fitness"),
    ("sedentary-period", Kind.INTERVAL, "activity_and_fitness"),
    ("swim-lengths-data", Kind.INTERVAL, "activity_and_fitness"),
    ("time-in-heart-rate-zone", Kind.INTERVAL, "activity_and_fitness"),
    ("vo2-max", Kind.SAMPLE, "activity_and_fitness"),
    ("run-vo2-max", Kind.SAMPLE, "activity_and_fitness"),
    ("daily-vo2-max", Kind.DAILY, "activity_and_fitness"),
    ("exercise", Kind.SESSION_CIVIL, "activity_and_fitness"),
    # health_metrics_and_measurements
    ("heart-rate", Kind.SAMPLE, "health_metrics_and_measurements"),
    ("heart-rate-variability", Kind.SAMPLE, "health_metrics_and_measurements"),
    ("weight", Kind.SAMPLE, "health_metrics_and_measurements"),
    ("height", Kind.SAMPLE, "health_metrics_and_measurements"),
    ("body-fat", Kind.SAMPLE, "health_metrics_and_measurements"),
    ("blood-glucose", Kind.SAMPLE, "health_metrics_and_measurements"),
    ("oxygen-saturation", Kind.SAMPLE, "health_metrics_and_measurements"),
    ("core-body-temperature", Kind.SAMPLE, "health_metrics_and_measurements"),
    ("respiratory-rate-sleep-summary", Kind.SAMPLE, "health_metrics_and_measurements"),
    ("daily-heart-rate-variability", Kind.DAILY, "health_metrics_and_measurements"),
    ("daily-heart-rate-zones", Kind.DAILY, "health_metrics_and_measurements"),
    ("daily-oxygen-saturation", Kind.DAILY, "health_metrics_and_measurements"),
    ("daily-respiratory-rate", Kind.DAILY, "health_metrics_and_measurements"),
    ("daily-resting-heart-rate", Kind.DAILY, "health_metrics_and_measurements"),
    ("daily-sleep-temperature-derivations", Kind.DAILY, "health_metrics_and_measurements"),
    # sleep
    ("sleep", Kind.SESSION_SLEEP, "sleep"),
    # nutrition
    ("hydration-log", Kind.SESSION_CIVIL, "nutrition"),
    ("nutrition-log", Kind.SESSION_CIVIL, "nutrition"),
    # ecg
    ("electrocardiogram", Kind.SESSION_ECG, "ecg"),
    # irn
    ("irregular-rhythm-notification", Kind.SESSION_CIVIL, "irn"),
]

REGISTRY: dict[str, DataType] = {
    data_id: _make(data_id, kind, scope_suffix) for data_id, kind, scope_suffix in _ENTRIES
}


def resolve(requested: str | list[str]) -> list[DataType]:
    """Turn config's `data_types` value ("all" or a list of ids) into DataTypes.

    Raises ValueError naming any id that isn't in REGISTRY, so a typo in
    config.toml fails loudly instead of silently exporting nothing for it.
    """
    if requested == "all":
        return list(REGISTRY.values())
    unknown = [data_id for data_id in requested if data_id not in REGISTRY]
    if unknown:
        raise ValueError(f"Unknown data type id(s): {', '.join(unknown)}")
    return [REGISTRY[data_id] for data_id in requested]
