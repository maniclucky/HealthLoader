import csv
import json
from datetime import date
from pathlib import Path

import pytest

from fitthing.aggregate import build_combined_rows, build_daily_rows, reference_weight, write_daily_csv
from fitthing.config import Config


def make_config(
    tmp_path: Path,
    csv_device_packages: list[str] | None = None,
    csv_days: int = 30,
    csv_columns: str | list[str] = "all",
    target_settings: dict | None = None,
) -> Config:
    target_settings_path = tmp_path / "targetSettings.json"
    if target_settings is not None:
        target_settings_path.write_text(json.dumps(target_settings))
    return Config(
        client_secret_path=tmp_path / "secrets" / "client_secret.json",
        token_path=tmp_path / "secrets" / "token.json",
        output_dir=tmp_path / "data",
        start_date=None,
        data_types="all",
        window_days=14,
        request_delay_seconds=0.0,
        csv_device_packages=["ringconn"] if csv_device_packages is None else csv_device_packages,
        csv_days=csv_days,
        target_settings_path=target_settings_path,
        csv_columns=csv_columns,
    )


def _write_daily_nutrition(output_dir: Path, entries: dict[str, dict]) -> None:
    path = output_dir / "nutrition" / "daily.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries))


def _write_window(raw_dir: Path, data_type_id: str, start: str, end: str, fetched_at: str, points: list[dict]) -> None:
    path = raw_dir / data_type_id / f"{start}_{end}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "data_type": data_type_id,
                "window_start": start,
                "window_end": end,
                "fetched_at": fetched_at,
                "filter": "",
                "count": len(points),
                "dataPoints": points,
            }
        )
    )


def _weight_point(year, month, day, grams, package="ringconn"):
    return {
        "dataSource": {"application": {"packageName": package}},
        "weight": {
            "sampleTime": {"civilTime": {"date": {"year": year, "month": month, "day": day}}},
            "weightGrams": grams,
        },
    }


def _body_fat_point(year, month, day, percentage, package="ringconn"):
    return {
        "dataSource": {"application": {"packageName": package}},
        "bodyFat": {
            "sampleTime": {"civilTime": {"date": {"year": year, "month": month, "day": day}}},
            "percentage": percentage,
        },
    }


def _steps_point(year, month, day, count, package="ringconn"):
    return {
        "dataSource": {"application": {"packageName": package}},
        "steps": {
            "interval": {"civilStartTime": {"date": {"year": year, "month": month, "day": day}}},
            "count": str(count),
        },
    }


def _energy_point(year, month, day, kcal, package="ringconn"):
    return {
        "dataSource": {"application": {"packageName": package}},
        "activeEnergyBurned": {
            "interval": {"civilStartTime": {"date": {"year": year, "month": month, "day": day}}},
            "kcal": kcal,
        },
    }


# --- build_daily_rows: per-type extraction ---


def test_weight_converts_grams_to_lbs_and_averages_same_day_samples(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_window(
        raw_dir,
        "weight",
        "2026-01-01",
        "2026-01-02",
        "2026-01-02T00:00:00Z",
        [_weight_point(2026, 1, 1, 100000), _weight_point(2026, 1, 1, 100200)],
    )

    rows = build_daily_rows(raw_dir, device_packages=[])

    expected = ((100000 * 0.00220462262185) + (100200 * 0.00220462262185)) / 2
    assert rows[date(2026, 1, 1)]["Weight (lbs)"] == expected


def test_steps_summed_per_day(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_window(
        raw_dir,
        "steps",
        "2026-01-01",
        "2026-01-02",
        "2026-01-02T00:00:00Z",
        [_steps_point(2026, 1, 1, 100), _steps_point(2026, 1, 1, 250)],
    )

    rows = build_daily_rows(raw_dir, device_packages=[])

    assert rows[date(2026, 1, 1)]["Steps"] == 350


def test_device_filter_drops_other_sources_for_filtered_types(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_window(
        raw_dir,
        "steps",
        "2026-01-01",
        "2026-01-02",
        "2026-01-02T00:00:00Z",
        [_steps_point(2026, 1, 1, 100, package="ringconn"), _steps_point(2026, 1, 1, 999, package="other-app")],
    )

    rows = build_daily_rows(raw_dir, device_packages=["ringconn"])

    assert rows[date(2026, 1, 1)]["Steps"] == 100


def test_empty_device_filter_includes_every_source(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_window(
        raw_dir,
        "steps",
        "2026-01-01",
        "2026-01-02",
        "2026-01-02T00:00:00Z",
        [_steps_point(2026, 1, 1, 100, package="ringconn"), _steps_point(2026, 1, 1, 50, package="other-app")],
    )

    rows = build_daily_rows(raw_dir, device_packages=[])

    assert rows[date(2026, 1, 1)]["Steps"] == 150


def test_weight_and_body_fat_are_never_filtered_by_device(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_window(
        raw_dir,
        "weight",
        "2026-01-01",
        "2026-01-02",
        "2026-01-02T00:00:00Z",
        [_weight_point(2026, 1, 1, 100000, package="some-other-scale-app")],
    )

    rows = build_daily_rows(raw_dir, device_packages=["ringconn"])

    assert rows[date(2026, 1, 1)]["Weight (lbs)"] is not None


def test_missing_data_type_directory_leaves_column_empty(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_window(
        raw_dir,
        "steps",
        "2026-01-01",
        "2026-01-02",
        "2026-01-02T00:00:00Z",
        [_steps_point(2026, 1, 1, 100)],
    )
    # no weight/, body-fat/, or active-energy-burned/ dirs at all

    rows = build_daily_rows(raw_dir, device_packages=[])

    assert rows[date(2026, 1, 1)]["Weight (lbs)"] is None
    assert rows[date(2026, 1, 1)]["Steps"] == 100


# --- overlapping windows: freshest fetch wins, no double-counting ---


def test_overlapping_windows_do_not_double_count_the_shared_day(tmp_path):
    raw_dir = tmp_path / "raw"
    # First run: a window covering Jan 1-5.
    _write_window(
        raw_dir,
        "steps",
        "2026-01-01",
        "2026-01-05",
        "2026-01-05T00:00:00Z",
        [_steps_point(2026, 1, 4, 1000)],
    )
    # Incremental run re-fetches the overlap (STATE_OVERLAP_DAYS), producing
    # a second file whose range also covers Jan 4, fetched later.
    _write_window(
        raw_dir,
        "steps",
        "2026-01-03",
        "2026-01-06",
        "2026-01-06T00:00:00Z",
        [_steps_point(2026, 1, 4, 1234)],
    )

    rows = build_daily_rows(raw_dir, device_packages=[])

    # The fresher file's value wins outright — not summed with the stale one.
    assert rows[date(2026, 1, 4)]["Steps"] == 1234


def test_older_file_still_owns_days_outside_the_newer_files_range(tmp_path):
    raw_dir = tmp_path / "raw"
    _write_window(
        raw_dir,
        "steps",
        "2026-01-01",
        "2026-01-05",
        "2026-01-05T00:00:00Z",
        [_steps_point(2026, 1, 1, 500), _steps_point(2026, 1, 4, 1000)],
    )
    _write_window(
        raw_dir,
        "steps",
        "2026-01-03",
        "2026-01-06",
        "2026-01-06T00:00:00Z",
        [_steps_point(2026, 1, 4, 1234)],
    )

    rows = build_daily_rows(raw_dir, device_packages=[])

    assert rows[date(2026, 1, 1)]["Steps"] == 500  # untouched by the newer, non-overlapping file
    assert rows[date(2026, 1, 4)]["Steps"] == 1234  # overlap: fresher file wins


# --- build_combined_rows: fixed window, nutrition merge, interpolation, target-fill ---


def test_window_always_has_csv_days_rows_even_with_no_data_anywhere(tmp_path):
    config = make_config(tmp_path, csv_days=5)

    rows = build_combined_rows(config, end=date(2026, 1, 10))

    assert sorted(rows) == [date(2026, 1, d) for d in range(6, 11)]
    for row in rows.values():
        assert all(v is None for v in row.values())


def test_nutrition_columns_come_from_daily_json(tmp_path):
    config = make_config(tmp_path, csv_days=3)
    _write_daily_nutrition(
        config.output_dir,
        {"2026-01-10": {"calories_kcal": 2000.0, "protein_g": 150.0, "carbs_g": 200.0, "fat_g": 60.0}},
    )

    rows = build_combined_rows(config, end=date(2026, 1, 10))

    row = rows[date(2026, 1, 10)]
    assert row["Energy (kcal)"] == 2000.0
    assert row["Protein (g)"] == 150.0
    assert row["Carbs (g)"] == 200.0
    assert row["Fat (g)"] == 60.0


def test_target_settings_fill_missing_nutrition_days_only(tmp_path):
    config = make_config(
        tmp_path, csv_days=2, target_settings={"calories": 2200, "protein": 150, "carbs": 250, "fat": 70}
    )
    _write_daily_nutrition(
        config.output_dir,
        {"2026-01-10": {"calories_kcal": 1000.0, "protein_g": 80.0, "carbs_g": 90.0, "fat_g": 30.0}},
    )

    rows = build_combined_rows(config, end=date(2026, 1, 10))

    # 2026-01-09 has no logged nutrition at all: filled from targets.
    filled = rows[date(2026, 1, 9)]
    assert filled["Energy (kcal)"] == 2200.0
    assert filled["Protein (g)"] == 150.0
    assert filled["Carbs (g)"] == 250.0
    assert filled["Fat (g)"] == 70.0
    # 2026-01-10 was actually logged: targets must not override it.
    logged = rows[date(2026, 1, 10)]
    assert logged["Energy (kcal)"] == 1000.0


def test_missing_target_settings_file_leaves_nutrition_blank(tmp_path):
    config = make_config(tmp_path, csv_days=1)  # target_settings=None -> file never written

    rows = build_combined_rows(config, end=date(2026, 1, 10))

    assert rows[date(2026, 1, 10)]["Energy (kcal)"] is None


def test_weight_gap_is_linearly_interpolated_between_known_neighbors(tmp_path):
    config = make_config(tmp_path, csv_days=3)
    _write_window(
        config.raw_dir,
        "weight",
        "2026-01-08",
        "2026-01-11",
        "2026-01-11T00:00:00Z",
        [_weight_point(2026, 1, 8, 100000), _weight_point(2026, 1, 10, 102000)],  # Jan 9 missing
    )

    rows = build_combined_rows(config, end=date(2026, 1, 10))

    left = 100000 * 0.00220462262185
    right = 102000 * 0.00220462262185
    assert rows[date(2026, 1, 9)]["Weight (lbs)"] == left + 0.5 * (right - left)


def test_leading_and_trailing_gaps_are_not_interpolated(tmp_path):
    config = make_config(tmp_path, csv_days=3)
    _write_window(
        config.raw_dir,
        "weight",
        "2026-01-09",
        "2026-01-10",
        "2026-01-10T00:00:00Z",
        [_weight_point(2026, 1, 9, 100000)],  # only the middle day has data
    )

    rows = build_combined_rows(config, end=date(2026, 1, 10))

    assert rows[date(2026, 1, 8)]["Weight (lbs)"] is None  # leading: no left neighbor
    assert rows[date(2026, 1, 9)]["Weight (lbs)"] is not None
    assert rows[date(2026, 1, 10)]["Weight (lbs)"] is None  # trailing: no right neighbor


def test_steps_and_energy_burned_are_never_filled(tmp_path):
    config = make_config(tmp_path, csv_days=1, target_settings={"steps": 10000, "calories": 2000})

    rows = build_combined_rows(config, end=date(2026, 1, 10))

    assert rows[date(2026, 1, 10)]["Steps"] is None
    assert rows[date(2026, 1, 10)]["Energy Burned (kcal)"] is None


# --- write_daily_csv ---


def test_write_daily_csv_writes_header_and_blank_cells_for_missing_values(tmp_path):
    config = make_config(tmp_path, csv_days=1)
    _write_window(
        config.raw_dir,
        "steps",
        "2026-01-01",
        "2026-01-02",
        "2026-01-02T00:00:00Z",
        [_steps_point(2026, 1, 1, 100, package="ringconn")],
    )

    path, n = write_daily_csv(config, end=date(2026, 1, 1))

    assert n == 1
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == [
        "Date",
        "Energy (kcal)",
        "Protein (g)",
        "Carbs (g)",
        "Fat (g)",
        "Weight (lbs)",
        "Body Fat (%)",
        "Steps",
        "Energy Burned (kcal)",
        "Avg Heart Rate (bpm)",
        "Resting Heart Rate (bpm)",
        "SpO2 (%)",
        "Sleep (hrs)",
        "Exercise (min)",
        "Energy vs Target (kcal)",
        "Protein vs Target (g)",
        "Carbs vs Target (g)",
        "Fat vs Target (g)",
        "Steps vs Target",
        "Weight Change 7d (lbs)",
    ]
    assert rows[1] == ["2026-01-01", "", "", "", "", "", "", "100"] + [""] * 12


def test_write_daily_csv_with_no_data_still_writes_the_full_window_blank(tmp_path):
    config = make_config(tmp_path, csv_days=3)

    path, n = write_daily_csv(config, end=date(2026, 1, 10))

    assert n == 3
    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 4  # header + 3 blank day rows
    assert rows[1][0] == "2026-01-08"
    assert rows[3][0] == "2026-01-10"


def test_write_daily_csv_is_a_full_rebuild_not_an_incremental_merge(tmp_path):
    config = make_config(tmp_path, csv_days=2)

    path, n = write_daily_csv(config, end=date(2026, 1, 10))
    with path.open(newline="") as f:
        assert list(csv.reader(f))[1][7] == ""  # Steps blank: no data yet

    # A second run with new raw data for the same window should replace,
    # not append to, the first CSV — same row count, updated value.
    _write_window(
        config.raw_dir,
        "steps",
        "2026-01-09",
        "2026-01-10",
        "2026-01-10T00:00:00Z",
        [_steps_point(2026, 1, 9, 200, package="ringconn")],
    )
    path, n = write_daily_csv(config, end=date(2026, 1, 10))

    with path.open(newline="") as f:
        rows = list(csv.reader(f))
    assert n == 2
    assert len(rows) == 3  # header + two day rows, not four
    assert rows[1][7] == "200"


def _local_interval(start: str, end: str, offset: str = "-18000s") -> dict:
    return {"startTime": start, "startUtcOffset": offset, "endTime": end, "endUtcOffset": offset}


def test_extra_google_columns_aggregate_per_day(tmp_path):
    config = make_config(tmp_path, csv_days=1)
    civil = {"date": {"year": 2026, "month": 1, "day": 1}}
    _write_window(config.raw_dir, "heart-rate", "2026-01-01", "2026-01-02", "2026-01-02T00:00:00Z", [
        {"dataSource": {}, "heartRate": {"sampleTime": {"civilTime": civil}, "beatsPerMinute": "60"}},
        {"dataSource": {}, "heartRate": {"sampleTime": {"civilTime": civil}, "beatsPerMinute": "80"}},
    ])
    _write_window(config.raw_dir, "daily-resting-heart-rate", "2026-01-01", "2026-01-02", "2026-01-02T00:00:00Z", [
        {"dataSource": {}, "dailyRestingHeartRate": {"date": civil["date"], "beatsPerMinute": "55"}},
    ])
    _write_window(config.raw_dir, "daily-oxygen-saturation", "2026-01-01", "2026-01-02", "2026-01-02T00:00:00Z", [
        {"dataSource": {}, "dailyOxygenSaturation": {"date": civil["date"], "averagePercentage": 96.5}},
    ])
    _write_window(config.raw_dir, "exercise", "2026-01-01", "2026-01-02", "2026-01-02T00:00:00Z", [
        {"dataSource": {}, "exercise": {"interval": _local_interval("2026-01-01T15:00:00Z", "2026-01-01T16:00:00Z")}},
        # Overlaps the first by 15 min (phone + treadmill logging one walk):
        # counted once, not summed.
        {"dataSource": {}, "exercise": {"interval": _local_interval("2026-01-01T15:45:00Z", "2026-01-01T16:15:00Z")}},
        {"dataSource": {}, "exercise": {"interval": _local_interval("2026-01-01T20:00:00Z", "2026-01-01T20:15:00Z")}},
    ])

    row = build_combined_rows(config, end=date(2026, 1, 1))[date(2026, 1, 1)]

    assert row["Avg Heart Rate (bpm)"] == 70
    assert row["Resting Heart Rate (bpm)"] == 55
    assert row["SpO2 (%)"] == 96.5
    assert row["Exercise (min)"] == 90


def test_sleep_counts_toward_the_local_day_it_ended(tmp_path):
    config = make_config(tmp_path, csv_days=2)
    # 22:00 Jan 1 to 06:00 Jan 2 local (UTC-5): credited to Jan 2, the
    # morning it ended, along with that afternoon's nap.
    night = {"dataSource": {}, "sleep": {
        "interval": _local_interval("2026-01-02T03:00:00Z", "2026-01-02T11:00:00Z"),
        "summary": {"minutesAsleep": "450"},
    }}
    nap = {"dataSource": {}, "sleep": {
        "interval": _local_interval("2026-01-02T19:00:00Z", "2026-01-02T19:30:00Z"),
        "summary": {"minutesAsleep": "30"},
    }}
    _write_window(config.raw_dir, "sleep", "2026-01-01", "2026-01-03", "2026-01-03T00:00:00Z", [night, nap])

    rows = build_combined_rows(config, end=date(2026, 1, 2))

    assert rows[date(2026, 1, 1)]["Sleep (hrs)"] is None
    assert rows[date(2026, 1, 2)]["Sleep (hrs)"] == pytest.approx(8.0)


def test_extra_cronometer_nutrients_become_columns_after_the_core_ones(tmp_path):
    config = make_config(tmp_path, csv_days=2, target_settings={"calories": 2500})
    _write_daily_nutrition(config.output_dir, {
        "2026-01-01": {"calories_kcal": 2000, "protein_g": 100, "carbs_g": 200, "fat_g": 60,
                       "nutrients": {"Sodium (mg)": 2300, "Fiber (g)": 30}},
    })

    path, _ = write_daily_csv(config, end=date(2026, 1, 2))

    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0])[-2:] == ["Fiber (g)", "Sodium (mg)"]
    assert rows[0]["Sodium (mg)"] == "2300.00"
    # Unlogged day: core calories are target-filled, extra nutrients stay blank.
    assert rows[1]["Energy (kcal)"] == "2500.00"
    assert rows[1]["Sodium (mg)"] == ""


# --- steps: ring primary, treadmill replaces it while running ---


def _timed_steps(start: str, end: str, count: int, package: str) -> dict:
    interval = _local_interval(start, end)
    interval["civilStartTime"] = {"date": {"year": 2026, "month": 1, "day": 1}}
    return {"dataSource": {"application": {"packageName": package}}, "steps": {"interval": interval, "count": str(count)}}


def _steps_window(raw_dir: Path, points: list[dict]) -> None:
    _write_window(raw_dir, "steps", "2026-01-01", "2026-01-02", "2026-01-02T00:00:00Z", points)


def test_treadmill_count_replaces_ring_during_the_session(tmp_path):
    raw_dir = tmp_path / "raw"
    _steps_window(raw_dir, [
        _timed_steps("2026-01-01T14:00:00Z", "2026-01-01T14:30:00Z", 300, "ringconn"),  # before: kept
        _timed_steps("2026-01-01T15:00:00Z", "2026-01-01T15:30:00Z", 1000, "ringconn"),  # inside: dropped
        _timed_steps("2026-01-01T15:30:00Z", "2026-01-01T16:00:00Z", 400, "ringconn"),  # half inside: half kept
        _timed_steps("2026-01-01T15:00:00Z", "2026-01-01T15:45:00Z", 4000, "treadmill"),
        _timed_steps("2026-01-01T15:10:00Z", "2026-01-01T15:20:00Z", 999, "phone"),  # not a primary source
    ])

    rows = build_daily_rows(raw_dir, device_packages=["ringconn"], treadmill_packages=["treadmill"])

    assert rows[date(2026, 1, 1)]["Steps"] == pytest.approx(300 + 200 + 4000)


def test_overlapping_treadmill_records_are_one_session(tmp_path):
    raw_dir = tmp_path / "raw"
    # Same session uploaded twice with shifted start times (seen live).
    _steps_window(raw_dir, [
        _timed_steps("2026-01-01T13:00:00Z", "2026-01-01T14:30:00Z", 10050, "treadmill"),
        _timed_steps("2026-01-01T14:00:00Z", "2026-01-01T15:30:00Z", 10050, "treadmill"),
        _timed_steps("2026-01-01T13:30:00Z", "2026-01-01T14:00:00Z", 500, "ringconn"),
    ])

    rows = build_daily_rows(raw_dir, device_packages=["ringconn"], treadmill_packages=["treadmill"])

    assert rows[date(2026, 1, 1)]["Steps"] == 10050


def test_no_treadmill_packages_uses_the_primary_source_only(tmp_path):
    raw_dir = tmp_path / "raw"
    _steps_window(raw_dir, [
        _timed_steps("2026-01-01T15:00:00Z", "2026-01-01T15:30:00Z", 1000, "ringconn"),
        _timed_steps("2026-01-01T15:00:00Z", "2026-01-01T15:45:00Z", 4000, "treadmill"),
    ])

    rows = build_daily_rows(raw_dir, device_packages=["ringconn"], treadmill_packages=[])

    assert rows[date(2026, 1, 1)]["Steps"] == 1000


# --- csv_columns: which columns get written ---


def test_default_config_writes_the_summary_columns(tmp_path):
    from fitthing.config import DEFAULTS

    config = make_config(tmp_path, csv_days=1, csv_columns=DEFAULTS["csv_columns"])

    path, _ = write_daily_csv(config, end=date(2026, 1, 1))

    with path.open(newline="") as f:
        header = next(csv.reader(f))
    assert header == [
        "Date",
        "Energy (kcal)",
        "Protein (g)",
        "Carbs (g)",
        "Fat (g)",
        "Weight (lbs)",
        "Body Fat (%)",
        "Steps",
        "Energy vs Target (kcal)",
        "Protein vs Target (g)",
        "Carbs vs Target (g)",
        "Fat vs Target (g)",
        "Steps vs Target",
        "Weight Change 7d (lbs)",
    ]


def test_csv_columns_can_name_a_cronometer_nutrient(tmp_path):
    config = make_config(tmp_path, csv_days=1, csv_columns=["Steps", "Sodium (mg)"])
    _write_daily_nutrition(config.output_dir, {"2026-01-01": {"nutrients": {"Sodium (mg)": 2300}}})

    path, _ = write_daily_csv(config, end=date(2026, 1, 1))

    with path.open(newline="") as f:
        assert list(csv.reader(f)) == [["Date", "Steps", "Sodium (mg)"], ["2026-01-01", "", "2300.00"]]


def test_unknown_csv_column_raises_listing_the_valid_names(tmp_path):
    config = make_config(tmp_path, csv_days=1, csv_columns=["Stepz"])

    with pytest.raises(ValueError) as exc_info:
        write_daily_csv(config, end=date(2026, 1, 1))

    assert "Stepz" in str(exc_info.value)
    assert "Steps" in str(exc_info.value)


# --- target deltas ---


def test_target_deltas_are_value_minus_target(tmp_path):
    config = make_config(
        tmp_path,
        csv_days=1,
        target_settings={"calories": 2500, "protein": 180, "carbs": 300, "fat": 70, "steps": 10000},
    )
    _write_daily_nutrition(
        config.output_dir,
        {"2026-01-01": {"calories_kcal": 2600, "protein_g": 150, "carbs_g": 300, "fat_g": 80}},
    )
    _write_window(
        config.raw_dir, "steps", "2026-01-01", "2026-01-02", "2026-01-02T00:00:00Z",
        [_steps_point(2026, 1, 1, 8000)],
    )

    row = build_combined_rows(config, end=date(2026, 1, 1))[date(2026, 1, 1)]

    assert row["Energy vs Target (kcal)"] == 100
    assert row["Protein vs Target (g)"] == -30
    assert row["Carbs vs Target (g)"] == 0
    assert row["Fat vs Target (g)"] == 10
    assert row["Steps vs Target"] == -2000


def test_target_deltas_blank_without_a_value_or_a_target(tmp_path):
    config = make_config(tmp_path, csv_days=1, target_settings={"calories": 2500})

    row = build_combined_rows(config, end=date(2026, 1, 1))[date(2026, 1, 1)]

    # Calories were target-filled (unlogged day), so the delta is 0.
    assert row["Energy vs Target (kcal)"] == 0
    # No protein target, no steps logged.
    assert row["Protein vs Target (g)"] is None
    assert row["Steps vs Target"] is None


# --- 7-day weight change ---


def test_reference_weight_uses_the_day_exactly_seven_days_prior():
    measured = {date(2026, 1, 1): 200.0, date(2026, 1, 3): 199.0, date(2026, 1, 7): 198.0}

    assert reference_weight(measured, date(2026, 1, 8)) == 200.0


def test_reference_weight_falls_back_to_the_nearest_day():
    measured = {date(2026, 1, 1): 200.0, date(2026, 1, 4): 199.0}

    # Target Jan 3: Jan 4 is 1 day away, Jan 1 is 2.
    assert reference_weight(measured, date(2026, 1, 10)) == 199.0


def test_reference_weight_tie_favors_the_more_recent_day():
    measured = {date(2026, 1, 2): 200.0, date(2026, 1, 4): 199.0}

    # Target Jan 3: both are 1 day away.
    assert reference_weight(measured, date(2026, 1, 10)) == 199.0


def test_reference_weight_ignores_the_day_itself_and_later():
    measured = {date(2026, 1, 10): 200.0, date(2026, 1, 11): 199.0}

    assert reference_weight(measured, date(2026, 1, 10)) is None


def test_weight_change_looks_back_before_the_csv_window(tmp_path):
    config = make_config(tmp_path, csv_days=1)
    _write_window(
        config.raw_dir, "weight", "2026-01-01", "2026-01-09", "2026-01-09T00:00:00Z",
        [_weight_point(2026, 1, 1, 100000), _weight_point(2026, 1, 8, 99000)],
    )

    row = build_combined_rows(config, end=date(2026, 1, 8))[date(2026, 1, 8)]

    assert row["Weight Change 7d (lbs)"] == pytest.approx(-1000 * 0.00220462262185)
