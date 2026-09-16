"""Date-range chunking and AIP-160 filter string construction."""

from __future__ import annotations

from datetime import date, timedelta

from fitthing.datatypes import DataType, TimeFormat


def iter_windows(start: date, end: date, days: int):
    """Yield half-open [window_start, window_end) date pairs covering [start, end).

    Each window is `days` long except possibly the last, which is truncated
    to `end`. Yields nothing if start >= end.
    """
    if days <= 0:
        raise ValueError("days must be positive")

    step = timedelta(days=days)
    current = start
    while current < end:
        window_end = min(current + step, end)
        yield current, window_end
        current = window_end


def _format_boundary(d: date, time_format: TimeFormat) -> str:
    if time_format is TimeFormat.DATE:
        return d.isoformat()
    if time_format is TimeFormat.CIVIL:
        return f"{d.isoformat()}T00:00:00"
    if time_format is TimeFormat.RFC3339:
        return f"{d.isoformat()}T00:00:00Z"
    raise ValueError(f"unhandled time format: {time_format}")  # pragma: no cover


def build_filter(data_type: DataType, start: date, end: date) -> str:
    """Build the AIP-160 filter string for one [start, end) window."""
    a = _format_boundary(start, data_type.time_format)
    b = _format_boundary(end, data_type.time_format)
    return f'{data_type.filter_field} >= "{a}" AND {data_type.filter_field} < "{b}"'
