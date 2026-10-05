import json
from datetime import date, timedelta
from pathlib import Path

import pytest

from fitthing.config import Config, CronometerConfig
import fitthing.cronometer.export as cronometer_export
from fitthing.cronometer.export import (
    STATE_KEY,
    load_daily_json,
    load_state,
    merge_daily_nutrition,
    range_for,
    run_cronometer_export,
    run_cronometer_import,
)
from fitthing.cronometer.parse import DailyNutrition

CSV_TEXT = "Date,Energy (kcal),Protein (g),Carbs (g),Fat (g)\n2025-06-01,2000,100,200,60\n"


def make_config(tmp_path: Path, start_date: str | None = None) -> Config:
    return Config(
        client_secret_path=tmp_path / "secrets" / "client_secret.json",
        token_path=tmp_path / "secrets" / "token.json",
        output_dir=tmp_path / "data",
        start_date=None,
        data_types="all",
        window_days=14,
        request_delay_seconds=0.0,
        csv_device_packages=[],
        csv_days=30,
        target_settings_path=tmp_path / "targetSettings.json",
        cronometer=CronometerConfig(
            enabled=True,
            credentials_path=tmp_path / "secrets" / "cronometer.json",
            session_path=tmp_path / "secrets" / "cronometer_session.json",
            start_date=start_date,
            gwt_module_base="https://cronometer.com/cronometer/",
            gwt_permutation="deadbeef",
            request_delay_seconds=0.0,
        ),
    )


class FakeClient:
    def __init__(self, csv_text: str = CSV_TEXT):
        self.csv_text = csv_text
        self.calls = []

    def export_daily_summary(self, start: date, end: date) -> str:
        self.calls.append((start, end))
        return self.csv_text


# --- range_for ---


def test_first_run_backfills_default_30_days():
    config = CronometerConfig(
        enabled=True,
        credentials_path=Path("x"),
        session_path=Path("y"),
        start_date=None,
        gwt_module_base="b",
        gwt_permutation="p",
        request_delay_seconds=0.0,
    )
    until = date(2020, 9, 18)

    start, end = range_for(config, state={}, since=None, until=until, full=False)

    assert end == until
    assert start == until - timedelta(days=30)


def test_first_run_days_override_widens_backfill():
    config = CronometerConfig(
        enabled=True,
        credentials_path=Path("x"),
        session_path=Path("y"),
        start_date=None,
        gwt_module_base="b",
        gwt_permutation="p",
        request_delay_seconds=0.0,
    )
    until = date(2020, 9, 18)

    start, end = range_for(config, state={}, since=None, until=until, full=False, days=365)

    assert end == until
    assert start == until - timedelta(days=365)


def test_configured_start_date_wins_over_days_override():
    config = CronometerConfig(
        enabled=True,
        credentials_path=Path("x"),
        session_path=Path("y"),
        start_date="2025-01-01",
        gwt_module_base="b",
        gwt_permutation="p",
        request_delay_seconds=0.0,
    )

    start, end = range_for(
        config, state={}, since=None, until=date(2020, 9, 18), full=False, days=365
    )

    assert start == date(2025, 1, 1)


def test_first_run_uses_configured_start_date():
    config = CronometerConfig(
        enabled=True,
        credentials_path=Path("x"),
        session_path=Path("y"),
        start_date="2025-01-01",
        gwt_module_base="b",
        gwt_permutation="p",
        request_delay_seconds=0.0,
    )

    start, end = range_for(config, state={}, since=None, until=date(2020, 9, 18), full=False)

    assert start == date(2025, 1, 1)


def test_incremental_run_overlaps_3_days():
    config = CronometerConfig(
        enabled=True,
        credentials_path=Path("x"),
        session_path=Path("y"),
        start_date=None,
        gwt_module_base="b",
        gwt_permutation="p",
        request_delay_seconds=0.0,
    )
    state = {STATE_KEY: {"synced_through": "2020-09-10"}}

    start, end = range_for(config, state, since=None, until=date(2020, 9, 18), full=False)

    assert start == date(2020, 9, 7)
    assert end == date(2020, 9, 18)


def test_full_flag_ignores_state():
    config = CronometerConfig(
        enabled=True,
        credentials_path=Path("x"),
        session_path=Path("y"),
        start_date=None,
        gwt_module_base="b",
        gwt_permutation="p",
        request_delay_seconds=0.0,
    )
    state = {STATE_KEY: {"synced_through": "2020-09-10"}}

    start, end = range_for(config, state, since=None, until=date(2020, 9, 18), full=True)

    assert start == date(2020, 9, 18) - timedelta(days=30)


def test_until_defaults_to_yesterday_local_not_today():
    config = CronometerConfig(
        enabled=True,
        credentials_path=Path("x"),
        session_path=Path("y"),
        start_date=None,
        gwt_module_base="b",
        gwt_permutation="p",
        request_delay_seconds=0.0,
    )

    start, end = range_for(config, state={}, since=None, until=None, full=True)

    assert end == date.today() - timedelta(days=1)


def test_explicit_until_today_is_clamped_to_yesterday():
    config = CronometerConfig(
        enabled=True,
        credentials_path=Path("x"),
        session_path=Path("y"),
        start_date=None,
        gwt_module_base="b",
        gwt_permutation="p",
        request_delay_seconds=0.0,
    )
    today = date.today()

    start, end = range_for(config, state={}, since=None, until=today, full=True)

    assert end == today - timedelta(days=1)


def test_explicit_until_in_the_past_is_unaffected():
    config = CronometerConfig(
        enabled=True,
        credentials_path=Path("x"),
        session_path=Path("y"),
        start_date=None,
        gwt_module_base="b",
        gwt_permutation="p",
        request_delay_seconds=0.0,
    )

    start, end = range_for(config, state={}, since=None, until=date(2020, 1, 1), full=True)

    assert end == date(2020, 1, 1)


def test_since_overrides_state():
    config = CronometerConfig(
        enabled=True,
        credentials_path=Path("x"),
        session_path=Path("y"),
        start_date=None,
        gwt_module_base="b",
        gwt_permutation="p",
        request_delay_seconds=0.0,
    )
    state = {STATE_KEY: {"synced_through": "2020-09-10"}}

    start, end = range_for(
        config, state, since=date(2020, 1, 1), until=date(2020, 9, 18), full=False
    )

    assert start == date(2020, 1, 1)
    assert end == date(2020, 9, 18)


# --- merge_daily_nutrition ---


def test_merge_overwrites_only_dates_present_leaves_rest():
    existing = {
        "2025-06-01": {"calories_kcal": 1.0, "protein_g": 1.0, "carbs_g": 1.0, "fat_g": 1.0},
        "2025-06-02": {"calories_kcal": 2.0, "protein_g": 2.0, "carbs_g": 2.0, "fat_g": 2.0},
    }
    rows = [DailyNutrition(date="2025-06-01", calories_kcal=999.0, protein_g=1, carbs_g=1, fat_g=1)]

    merged = merge_daily_nutrition(existing, rows)

    assert merged["2025-06-01"]["calories_kcal"] == 999.0
    assert merged["2025-06-02"]["calories_kcal"] == 2.0


# --- run_cronometer_export ---


def test_export_writes_raw_csv_byte_identical(tmp_path):
    config = make_config(tmp_path)
    csv_text = "Date,Energy (kcal),Protein (g),Carbs (g),Fat (g)\r\n2025-06-01,2000,100,200,60\r\n"
    client = FakeClient(csv_text)

    result = run_cronometer_export(client, config, until=date(2025, 6, 1), full=True)

    raw_path = config.raw_dir / "cronometer-daily-summary" / f"{result.range_start.isoformat()}_{result.range_end.isoformat()}.csv"
    assert raw_path.read_bytes() == csv_text.encode("utf-8")


def test_export_merges_into_daily_json(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient()

    result = run_cronometer_export(client, config, until=date(2025, 6, 1), full=True)

    assert result.days_updated == 1
    daily = load_daily_json(config.output_dir / "nutrition" / "daily.json")
    assert daily["2025-06-01"] == {
        "calories_kcal": 2000.0,
        "protein_g": 100.0,
        "carbs_g": 200.0,
        "fat_g": 60.0,
    }


def test_export_advances_state(tmp_path):
    config = make_config(tmp_path)
    client = FakeClient()

    result = run_cronometer_export(client, config, until=date(2025, 6, 1), full=True)

    state = load_state(config.state_path)
    assert state[STATE_KEY]["synced_through"] == result.range_end.isoformat()


def test_crash_after_download_leaves_state_unadvanced(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    client = FakeClient()

    def boom(path, data):
        raise RuntimeError("disk full")

    monkeypatch.setattr(cronometer_export, "save_daily_json", boom)

    with pytest.raises(RuntimeError):
        run_cronometer_export(client, config, until=date(2025, 6, 1), full=True)

    state = load_state(config.state_path)
    assert STATE_KEY not in state
    # the raw CSV still landed on disk, since the crash was after that write
    raw_dir = config.raw_dir / "cronometer-daily-summary"
    assert any(raw_dir.iterdir())


def test_second_run_after_crash_recomputes_same_range_and_succeeds(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    client = FakeClient()

    def boom(path, data):
        raise RuntimeError("disk full")

    monkeypatch.setattr(cronometer_export, "save_daily_json", boom)
    with pytest.raises(RuntimeError):
        run_cronometer_export(client, config, until=date(2025, 6, 1), full=True)

    monkeypatch.undo()
    result = run_cronometer_export(client, config, until=date(2025, 6, 1), full=True)

    assert result.days_updated == 1
    daily = load_daily_json(config.output_dir / "nutrition" / "daily.json")
    assert "2025-06-01" in daily


# --- run_cronometer_import ---


def test_import_merges_hand_exported_csv(tmp_path):
    config = make_config(tmp_path)
    csv_path = tmp_path / "manual_export.csv"
    csv_path.write_text(CSV_TEXT)

    days_imported = run_cronometer_import(config, csv_path)

    assert days_imported == 1
    daily = load_daily_json(config.output_dir / "nutrition" / "daily.json")
    assert daily["2025-06-01"]["calories_kcal"] == 2000.0


def test_import_raises_value_error_on_missing_column(tmp_path):
    config = make_config(tmp_path)
    csv_path = tmp_path / "manual_export.csv"
    csv_path.write_text("Date,Energy (kcal),Carbs (g),Fat (g)\n2025-06-01,2000,200,60\n")

    with pytest.raises(ValueError):
        run_cronometer_import(config, csv_path)
