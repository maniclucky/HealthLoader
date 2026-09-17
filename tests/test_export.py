import json
from datetime import date
from pathlib import Path

import pytest

from fitthing.client import ApiError
from fitthing.config import Config
from fitthing.datatypes import REGISTRY
from fitthing.export import export_data_type, load_state, run_export, save_state


def make_config(tmp_path: Path, window_days: int = 5, start_date: str | None = "2026-01-01") -> Config:
    return Config(
        client_secret_path=tmp_path / "secrets" / "client_secret.json",
        token_path=tmp_path / "secrets" / "token.json",
        output_dir=tmp_path / "data",
        start_date=start_date,
        data_types="all",
        window_days=window_days,
        request_delay_seconds=0.0,
    )


class FakeClient:
    """Returns canned points per call; can simulate a crash after N calls."""

    def __init__(self, points_sequence=None, fail_after=None, fail_status=None):
        self.calls = []
        self._points_sequence = points_sequence or []
        self._fail_after = fail_after
        self._fail_status = fail_status

    def list_data_points(self, data_type_id, filter_str, page_size):
        self.calls.append((data_type_id, filter_str))
        call_index = len(self.calls) - 1
        if self._fail_after is not None and call_index >= self._fail_after:
            if self._fail_status is not None:
                raise ApiError(self._fail_status, "simulated API error")
            raise RuntimeError("simulated crash")
        if call_index < len(self._points_sequence):
            return self._points_sequence[call_index]
        return [{"n": call_index}]


def raw_files(config: Config, data_type_id: str) -> list[str]:
    d = config.raw_dir / data_type_id
    if not d.exists():
        return []
    return sorted(p.name for p in d.glob("*.json"))


def test_export_data_type_writes_files_and_state(tmp_path):
    config = make_config(tmp_path)
    steps = REGISTRY["steps"]
    client = FakeClient(points_sequence=[[{"a": 1}, {"a": 2}], [{"a": 3}], [{"a": 4}]])
    state = {}

    ok = export_data_type(
        client, config, steps, state, since=date(2026, 1, 1), until=date(2026, 1, 15), full=False
    )

    assert ok is True
    assert raw_files(config, "steps") == [
        "2026-01-01_2026-01-06.json",
        "2026-01-06_2026-01-11.json",
        "2026-01-11_2026-01-16.json",
    ]

    first = json.loads((config.raw_dir / "steps" / "2026-01-01_2026-01-06.json").read_text())
    assert first["data_type"] == "steps"
    assert first["window_start"] == "2026-01-01"
    assert first["window_end"] == "2026-01-06"
    assert first["count"] == 2
    assert first["dataPoints"] == [{"a": 1}, {"a": 2}]
    assert "filter" in first and "fetched_at" in first

    assert state["steps"]["synced_through"] == "2026-01-16"


def test_second_run_starts_from_overlap(tmp_path):
    config = make_config(tmp_path)
    steps = REGISTRY["steps"]

    # First run: 2026-01-01 .. 2026-01-15 inclusive (end exclusive 01-16).
    state = {}
    export_data_type(
        FakeClient(), config, steps, state, since=date(2026, 1, 1), until=date(2026, 1, 15), full=False
    )
    assert state["steps"]["synced_through"] == "2026-01-16"

    # Second run: no `since`, until moves to 2026-01-20. Should resume from
    # synced_through (01-16) minus the 3-day overlap = 01-13.
    client2 = FakeClient()
    export_data_type(
        client2, config, steps, state, since=None, until=date(2026, 1, 20), full=False
    )

    assert client2.calls[0][1].startswith('steps.interval.start_time >= "2026-01-13')
    assert state["steps"]["synced_through"] == "2026-01-21"
    assert raw_files(config, "steps") == [
        "2026-01-01_2026-01-06.json",
        "2026-01-06_2026-01-11.json",
        "2026-01-11_2026-01-16.json",
        "2026-01-13_2026-01-18.json",
        "2026-01-18_2026-01-21.json",
    ]


def test_third_identical_run_produces_no_duplicates(tmp_path):
    config = make_config(tmp_path)
    steps = REGISTRY["steps"]
    state = {}

    for _ in range(3):
        export_data_type(
            FakeClient(), config, steps, state,
            since=date(2026, 1, 1), until=date(2026, 1, 15), full=False,
        )

    assert raw_files(config, "steps") == [
        "2026-01-01_2026-01-06.json",
        "2026-01-06_2026-01-11.json",
        "2026-01-11_2026-01-16.json",
    ]
    assert state["steps"]["synced_through"] == "2026-01-16"


def test_interrupted_run_resumes_from_last_completed_window(tmp_path):
    config = make_config(tmp_path)
    steps = REGISTRY["steps"]
    state = {}

    # 3 windows total (01-01..06, 06..11, 11..16); crash on the 2nd call.
    crashing_client = FakeClient(fail_after=1)
    with pytest.raises(RuntimeError):
        export_data_type(
            crashing_client, config, steps, state,
            since=date(2026, 1, 1), until=date(2026, 1, 15), full=False,
        )

    assert raw_files(config, "steps") == ["2026-01-01_2026-01-06.json"]
    assert state["steps"]["synced_through"] == "2026-01-06"

    # Resume with no `since`: should pick up from synced_through - overlap.
    resumed_client = FakeClient()
    export_data_type(
        resumed_client, config, steps, state, since=None, until=date(2026, 1, 15), full=False
    )

    assert resumed_client.calls[0][1].startswith('steps.interval.start_time >= "2026-01-03')
    assert state["steps"]["synced_through"] == "2026-01-16"


def test_403_skips_type_and_returns_false(tmp_path):
    config = make_config(tmp_path)
    steps = REGISTRY["steps"]
    state = {}
    client = FakeClient(fail_after=0, fail_status=403)

    ok = export_data_type(
        client, config, steps, state, since=date(2026, 1, 1), until=date(2026, 1, 15), full=False
    )

    assert ok is False
    assert raw_files(config, "steps") == []


def test_non_403_api_error_propagates(tmp_path):
    config = make_config(tmp_path)
    steps = REGISTRY["steps"]
    state = {}
    client = FakeClient(fail_after=0, fail_status=500)

    with pytest.raises(ApiError):
        export_data_type(
            client, config, steps, state, since=date(2026, 1, 1), until=date(2026, 1, 15), full=False
        )


def test_full_ignores_existing_state(tmp_path):
    config = make_config(tmp_path, start_date="2026-01-10")
    steps = REGISTRY["steps"]
    state = {"steps": {"synced_through": "2026-01-16"}}

    client = FakeClient()
    export_data_type(
        client, config, steps, state, since=None, until=date(2026, 1, 15), full=True
    )

    # --full should backfill from config.start_date, ignoring synced_through.
    assert client.calls[0][1].startswith('steps.interval.start_time >= "2026-01-10')


def test_run_export_splits_by_granted_scope(tmp_path):
    config = make_config(tmp_path)
    data_types = [REGISTRY["steps"], REGISTRY["electrocardiogram"]]
    granted_scopes = {REGISTRY["steps"].scope}  # ecg scope not granted
    client = FakeClient()

    result = run_export(
        client, config, data_types, granted_scopes,
        since=date(2026, 1, 1), until=date(2026, 1, 2), full=False,
    )

    assert result.exported == ["steps"]
    assert result.skipped_ungranted == ["electrocardiogram"]
    assert result.failed == []


class TypeAwareFakeClient:
    """Fails for one specific data type id; succeeds normally for the rest.

    Models the real failure this was written to fix: `sleep` ran out of
    retries under sustained rate limiting, and everything after it in the
    registry (hydration-log, nutrition-log, electrocardiogram,
    irregular-rhythm-notification) never even started.
    """

    def __init__(self, fail_type_id, fail_status=500):
        self.calls = []
        self._fail_type_id = fail_type_id
        self._fail_status = fail_status

    def list_data_points(self, data_type_id, filter_str, page_size):
        self.calls.append(data_type_id)
        if data_type_id == self._fail_type_id:
            raise ApiError(self._fail_status, "simulated sustained rate limiting")
        return [{"n": len(self.calls)}]


def test_run_export_isolates_a_failing_type_and_continues(tmp_path):
    config = make_config(tmp_path)
    data_types = [REGISTRY["steps"], REGISTRY["sleep"], REGISTRY["weight"]]
    granted_scopes = {dt.scope for dt in data_types}
    client = TypeAwareFakeClient(fail_type_id="sleep")

    result = run_export(
        client, config, data_types, granted_scopes,
        since=date(2026, 1, 1), until=date(2026, 1, 2), full=False,
    )

    # steps comes before sleep and weight comes after it in the list passed
    # in; both must still run despite sleep failing in between.
    assert result.exported == ["steps", "weight"]
    assert result.failed == ["sleep"]
    assert raw_files(config, "steps") != []
    assert raw_files(config, "weight") != []
    assert raw_files(config, "sleep") == []


def test_state_round_trips(tmp_path):
    config = make_config(tmp_path)
    save_state(config.state_path, {"steps": {"synced_through": "2026-01-01"}})
    assert load_state(config.state_path) == {"steps": {"synced_through": "2026-01-01"}}


def test_load_state_missing_file_returns_empty_dict(tmp_path):
    config = make_config(tmp_path)
    assert load_state(config.state_path) == {}
