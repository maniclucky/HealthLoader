from datetime import date

import pytest

from fitthing.datatypes import REGISTRY
from fitthing.windows import build_filter, iter_windows


def test_iter_windows_full_windows():
    windows = list(iter_windows(date(2026, 1, 1), date(2026, 1, 15), days=7))
    assert windows == [
        (date(2026, 1, 1), date(2026, 1, 8)),
        (date(2026, 1, 8), date(2026, 1, 15)),
    ]


def test_iter_windows_partial_last_window():
    windows = list(iter_windows(date(2026, 1, 1), date(2026, 1, 10), days=7))
    assert windows == [
        (date(2026, 1, 1), date(2026, 1, 8)),
        (date(2026, 1, 8), date(2026, 1, 10)),  # truncated to 2 days
    ]


def test_iter_windows_start_equals_end_yields_nothing():
    assert list(iter_windows(date(2026, 1, 1), date(2026, 1, 1), days=7)) == []


def test_iter_windows_start_after_end_yields_nothing():
    assert list(iter_windows(date(2026, 1, 10), date(2026, 1, 1), days=7)) == []


def test_iter_windows_rejects_non_positive_days():
    with pytest.raises(ValueError):
        list(iter_windows(date(2026, 1, 1), date(2026, 1, 10), days=0))


def test_build_filter_interval():
    dt = REGISTRY["steps"]
    result = build_filter(dt, date(2026, 1, 1), date(2026, 1, 15))
    assert result == (
        'steps.interval.start_time >= "2026-01-01T00:00:00Z" '
        'AND steps.interval.start_time < "2026-01-15T00:00:00Z"'
    )


def test_build_filter_sample():
    dt = REGISTRY["weight"]
    result = build_filter(dt, date(2026, 1, 1), date(2026, 1, 15))
    assert result == (
        'weight.sample_time.physical_time >= "2026-01-01T00:00:00Z" '
        'AND weight.sample_time.physical_time < "2026-01-15T00:00:00Z"'
    )


def test_build_filter_daily():
    dt = REGISTRY["daily-resting-heart-rate"]
    result = build_filter(dt, date(2026, 1, 1), date(2026, 1, 15))
    assert result == (
        'daily_resting_heart_rate.date >= "2026-01-01" '
        'AND daily_resting_heart_rate.date < "2026-01-15"'
    )


def test_build_filter_session_civil():
    dt = REGISTRY["exercise"]
    result = build_filter(dt, date(2026, 1, 1), date(2026, 1, 15))
    assert result == (
        'exercise.interval.civil_start_time >= "2026-01-01T00:00:00" '
        'AND exercise.interval.civil_start_time < "2026-01-15T00:00:00"'
    )


def test_build_filter_session_sleep_uses_end_time():
    dt = REGISTRY["sleep"]
    result = build_filter(dt, date(2026, 1, 1), date(2026, 1, 15))
    assert result == (
        'sleep.interval.end_time >= "2026-01-01T00:00:00Z" '
        'AND sleep.interval.end_time < "2026-01-15T00:00:00Z"'
    )


def test_build_filter_session_ecg_uses_start_time():
    dt = REGISTRY["electrocardiogram"]
    result = build_filter(dt, date(2026, 1, 1), date(2026, 1, 15))
    assert result == (
        'electrocardiogram.interval.start_time >= "2026-01-01T00:00:00Z" '
        'AND electrocardiogram.interval.start_time < "2026-01-15T00:00:00Z"'
    )
