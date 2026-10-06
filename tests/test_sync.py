from datetime import date
from pathlib import Path

import fitthing.sync as sync
from fitthing.client import ApiError
from fitthing.config import Config
from fitthing.cronometer.auth import CronometerAuthError
from fitthing.cronometer.export import CronometerExportResult
from fitthing.export import ExportResult


def make_config(tmp_path: Path) -> Config:
    return Config(
        client_secret_path=tmp_path / "secrets" / "client_secret.json",
        token_path=tmp_path / "secrets" / "token.json",
        output_dir=tmp_path / "data",
        start_date=None,
        data_types="all",
        window_days=14,
        request_delay_seconds=0.0,
        csv_device_packages=[],
        csv_days=3,
        target_settings_path=tmp_path / "targetSettings.json",
    )


CRONOMETER_OK = CronometerExportResult(days_updated=2, range_start=date(2026, 1, 1), range_end=date(2026, 1, 2))


def _google_ok(calls):
    def pull(config, interactive, full, days):
        calls.append("google")
        return ExportResult(exported=["steps"], csv_path=config.output_dir / "health" / "daily.csv", csv_days=3)

    return pull


def test_runs_cronometer_before_google(tmp_path, monkeypatch):
    calls = []

    def pull_cronometer(config, interactive, full, days):
        calls.append("cronometer")
        return CRONOMETER_OK

    monkeypatch.setattr(sync, "_pull_cronometer", pull_cronometer)
    monkeypatch.setattr(sync, "_pull_google", _google_ok(calls))

    result = sync.run_sync(make_config(tmp_path), interactive=False)

    assert calls == ["cronometer", "google"]
    assert result.ok
    assert result.csv_days == 3


def test_passes_full_and_days_to_both_pulls(tmp_path, monkeypatch):
    seen = []

    def record(name, value):
        def pull(config, interactive, full, days):
            seen.append((name, full, days))
            return value

        return pull

    monkeypatch.setattr(sync, "_pull_cronometer", record("cronometer", CRONOMETER_OK))
    monkeypatch.setattr(sync, "_pull_google", record("google", ExportResult(csv_days=3)))

    sync.run_sync(make_config(tmp_path), interactive=False, full=True, days=90)

    assert seen == [("cronometer", True, 90), ("google", True, 90)]


def test_cronometer_failure_still_runs_google(tmp_path, monkeypatch):
    calls = []

    def pull_cronometer(config, interactive, full, days):
        raise CronometerAuthError("bad password")

    monkeypatch.setattr(sync, "_pull_cronometer", pull_cronometer)
    monkeypatch.setattr(sync, "_pull_google", _google_ok(calls))

    result = sync.run_sync(make_config(tmp_path), interactive=False)

    assert calls == ["google"]
    assert result.cronometer_error == "bad password"
    assert not result.ok


def test_missing_sign_in_is_recorded_not_raised(tmp_path, monkeypatch):
    def pull_cronometer(config, interactive, full, days):
        raise SystemExit(2)

    monkeypatch.setattr(sync, "_pull_cronometer", pull_cronometer)
    monkeypatch.setattr(sync, "_pull_google", _google_ok([]))

    result = sync.run_sync(make_config(tmp_path), interactive=False)

    assert result.cronometer is None
    assert "sign-in required" in result.cronometer_error


def test_google_failure_still_writes_the_csv(tmp_path, monkeypatch):
    def pull_google(config, interactive, full, days):
        raise ApiError(500, "boom")

    monkeypatch.setattr(sync, "_pull_cronometer", lambda *a: CRONOMETER_OK)
    monkeypatch.setattr(sync, "_pull_google", pull_google)
    config = make_config(tmp_path)

    result = sync.run_sync(config, interactive=False)

    assert result.google is None
    assert result.csv_path == config.output_dir / "health" / "daily.csv"
    assert result.csv_path.exists()
    assert result.csv_days == 3
    assert not result.ok


def test_a_failed_google_type_means_not_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(sync, "_pull_cronometer", lambda *a: CRONOMETER_OK)
    monkeypatch.setattr(sync, "_pull_google", lambda *a: ExportResult(failed=["sleep"]))

    assert not sync.run_sync(make_config(tmp_path), interactive=False).ok
