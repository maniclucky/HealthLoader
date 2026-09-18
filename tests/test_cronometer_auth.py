import json
import stat
from pathlib import Path

import pytest

from fitthing.config import CronometerConfig
from fitthing.cronometer.auth import (
    CronometerCredentials,
    resolve_credentials,
    save_credentials,
)


def make_cronometer_config(tmp_path: Path, credentials_path: Path | None = None) -> CronometerConfig:
    return CronometerConfig(
        enabled=True,
        credentials_path=credentials_path or (tmp_path / "cronometer.json"),
        session_path=tmp_path / "cronometer_session.json",
        start_date=None,
        gwt_module_base="https://cronometer.com/cronometer/",
        gwt_permutation="deadbeef",
        request_delay_seconds=0.0,
    )


def test_env_vars_beat_secrets_file(tmp_path, monkeypatch):
    config = make_cronometer_config(tmp_path)
    save_credentials(config.credentials_path, "from-file@example.com", "file-password")
    monkeypatch.setenv("CRONOMETER_USERNAME", "from-env@example.com")
    monkeypatch.setenv("CRONOMETER_PASSWORD", "env-password")

    creds = resolve_credentials(config, interactive=False)

    assert creds == CronometerCredentials(username="from-env@example.com", password="env-password")


def test_secrets_file_used_when_no_env_vars(tmp_path, monkeypatch):
    config = make_cronometer_config(tmp_path)
    save_credentials(config.credentials_path, "from-file@example.com", "file-password")
    monkeypatch.delenv("CRONOMETER_USERNAME", raising=False)
    monkeypatch.delenv("CRONOMETER_PASSWORD", raising=False)

    creds = resolve_credentials(config, interactive=False)

    assert creds == CronometerCredentials(username="from-file@example.com", password="file-password")


def test_save_credentials_writes_0600(tmp_path):
    path = tmp_path / "secrets" / "cronometer.json"

    save_credentials(path, "me@example.com", "s3cret")

    assert json.loads(path.read_text()) == {"username": "me@example.com", "password": "s3cret"}
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600


def test_non_interactive_with_nothing_available_exits_2(tmp_path, monkeypatch):
    config = make_cronometer_config(tmp_path)
    monkeypatch.delenv("CRONOMETER_USERNAME", raising=False)
    monkeypatch.delenv("CRONOMETER_PASSWORD", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        resolve_credentials(config, interactive=False)

    assert exc_info.value.code == 2


def test_interactive_prompt_used_when_nothing_saved(tmp_path, monkeypatch):
    config = make_cronometer_config(tmp_path)
    monkeypatch.delenv("CRONOMETER_USERNAME", raising=False)
    monkeypatch.delenv("CRONOMETER_PASSWORD", raising=False)
    monkeypatch.setattr("builtins.input", lambda _: "typed@example.com")
    monkeypatch.setattr("getpass.getpass", lambda _: "typed-password")

    creds = resolve_credentials(config, interactive=True)

    assert creds == CronometerCredentials(username="typed@example.com", password="typed-password")
