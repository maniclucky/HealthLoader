import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from google.auth.exceptions import RefreshError

from fitthing import auth
from fitthing.config import Config


def make_config(tmp_path: Path, client_secret_exists: bool = True) -> Config:
    if client_secret_exists:
        (tmp_path / "client_secret.json").write_text("{}")
    return Config(
        client_secret_path=tmp_path / "client_secret.json",
        token_path=tmp_path / "token.json",
        output_dir=tmp_path / "data",
        start_date=None,
        data_types="all",
        window_days=14,
        request_delay_seconds=0.0,
    )


class FakeCreds:
    def __init__(self, scopes, refresh_error=False, token="fake-token"):
        self.scopes = scopes
        self.token = token
        self._refresh_error = refresh_error
        self.refresh_calls = 0

    def refresh(self, request):
        self.refresh_calls += 1
        if self._refresh_error:
            raise RefreshError("token expired")


class FakeResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_missing_client_secret_exits_without_prompting(tmp_path, monkeypatch):
    config = make_config(tmp_path, client_secret_exists=False)

    def fail_if_called(*a, **k):
        raise AssertionError("must not prompt when client_secret.json is missing")

    monkeypatch.setattr("builtins.input", fail_if_called)

    with pytest.raises(SystemExit) as exc:
        auth.ensure_signed_in(config, ["scope-a"], interactive=True)
    assert exc.value.code == 2


def test_no_token_and_tty_prompts_then_runs_flow_on_yes(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    sentinel = object()

    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(auth, "run_browser_flow", lambda cfg, scopes: sentinel)

    result = auth.ensure_signed_in(config, ["scope-a"], interactive=True)
    assert result is sentinel


def test_refresh_error_and_tty_prompts_then_runs_flow(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    config.token_path.write_text("{}")
    sentinel = object()

    fake = FakeCreds(scopes=["scope-a"], refresh_error=True)
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", lambda path: fake)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(auth, "run_browser_flow", lambda cfg, scopes: sentinel)

    result = auth.ensure_signed_in(config, ["scope-a"], interactive=True)
    assert result is sentinel
    assert fake.refresh_calls == 1


def test_refresh_error_without_tty_exits_and_never_prompts(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    config.token_path.write_text("{}")

    fake = FakeCreds(scopes=["scope-a"], refresh_error=True)
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", lambda path: fake)

    def fail_if_called(*a, **k):
        raise AssertionError("must not prompt without a TTY")

    monkeypatch.setattr("builtins.input", fail_if_called)

    with pytest.raises(SystemExit) as exc:
        auth.ensure_signed_in(config, ["scope-a"], interactive=False)
    assert exc.value.code == 2


def test_declining_reauth_exits(tmp_path, monkeypatch):
    config = make_config(tmp_path)

    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    monkeypatch.setattr(
        auth,
        "run_browser_flow",
        lambda cfg, scopes: (_ for _ in ()).throw(AssertionError("must not run flow on 'n'")),
    )

    with pytest.raises(SystemExit) as exc:
        auth.ensure_signed_in(config, ["scope-a"], interactive=True)
    assert exc.value.code == 2


def test_insufficient_scopes_triggers_reauth(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    config.token_path.write_text("{}")
    sentinel = object()

    fake = FakeCreds(scopes=["scope-a"])  # missing "scope-b"
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", lambda path: fake)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(auth, "run_browser_flow", lambda cfg, scopes: sentinel)

    result = auth.ensure_signed_in(config, ["scope-a", "scope-b"], interactive=True)
    assert result is sentinel


def test_valid_token_returns_without_prompting(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    config.token_path.write_text("{}")

    fake = FakeCreds(scopes=["scope-a"])
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", lambda path: fake)
    monkeypatch.setattr(auth.requests, "get", lambda *a, **k: FakeResponse(200))

    def fail_if_called(*a, **k):
        raise AssertionError("must not prompt when the token is still valid")

    monkeypatch.setattr("builtins.input", fail_if_called)

    result = auth.ensure_signed_in(config, ["scope-a"], interactive=True)
    assert result is fake
    assert fake.refresh_calls == 1


def test_401_on_identity_check_triggers_reauth(tmp_path, monkeypatch):
    config = make_config(tmp_path)
    config.token_path.write_text("{}")
    sentinel = object()

    fake = FakeCreds(scopes=["scope-a"])
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", lambda path: fake)
    monkeypatch.setattr(auth.requests, "get", lambda *a, **k: FakeResponse(401))
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    monkeypatch.setattr(auth, "run_browser_flow", lambda cfg, scopes: sentinel)

    result = auth.ensure_signed_in(config, ["scope-a"], interactive=True)
    assert result is sentinel


def test_warning_printed_at_six_days(tmp_path, monkeypatch, capsys):
    config = make_config(tmp_path)
    config.token_path.write_text("{}")

    fake = FakeCreds(scopes=["scope-a"])
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", lambda path: fake)
    monkeypatch.setattr(auth.requests, "get", lambda *a, **k: FakeResponse(200))

    old = datetime.now(timezone.utc) - timedelta(days=6, hours=1)
    config.token_meta_path.write_text(
        json.dumps({"authorized_at": old.isoformat(), "scopes": ["scope-a"]})
    )

    result = auth.ensure_signed_in(config, ["scope-a"], interactive=True)
    assert result is fake
    err = capsys.readouterr().err
    assert "expires in about 1 day" in err


def test_no_warning_when_recently_authorized(tmp_path, monkeypatch, capsys):
    config = make_config(tmp_path)
    config.token_path.write_text("{}")

    fake = FakeCreds(scopes=["scope-a"])
    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", lambda path: fake)
    monkeypatch.setattr(auth.requests, "get", lambda *a, **k: FakeResponse(200))

    recent = datetime.now(timezone.utc) - timedelta(hours=1)
    config.token_meta_path.write_text(
        json.dumps({"authorized_at": recent.isoformat(), "scopes": ["scope-a"]})
    )

    auth.ensure_signed_in(config, ["scope-a"], interactive=True)
    err = capsys.readouterr().err
    assert "expires in about" not in err
