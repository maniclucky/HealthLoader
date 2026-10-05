import json
import stat
from pathlib import Path

import pytest
import responses

from fitthing import cli
from fitthing.config import CronometerConfig
from fitthing.cronometer import auth as cronometer_auth
from fitthing.cronometer.auth import (
    CronometerAuthError,
    CronometerCredentials,
    RPC_URL,
    resolve_credentials,
    save_credentials,
    sign_in,
)

LOGIN_PAGE_HTML = '<html><body><input type="hidden" value="tok-abc123" name="anticsrf"></body></html>'


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


# --- Phase 2: live login / authenticate / export-token flow ---


def test_extract_anticsrf_from_html_snippet():
    assert cronometer_auth._extract_anticsrf(LOGIN_PAGE_HTML) == "tok-abc123"


def test_extract_anticsrf_missing_raises():
    with pytest.raises(CronometerAuthError, match="anticsrf"):
        cronometer_auth._extract_anticsrf("<html><body>no token here</body></html>")


@responses.activate
def test_login_failure_raises_clear_error(tmp_path):
    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="wrong")
    responses.get(cronometer_auth.LOGIN_URL, body=LOGIN_PAGE_HTML)
    # No Set-Cookie: sesnonce on the response -> login did not succeed.
    responses.post(cronometer_auth.LOGIN_URL, body=LOGIN_PAGE_HTML)

    with pytest.raises(CronometerAuthError, match="2FA"):
        sign_in(config, creds)


@responses.activate
def test_unexpected_authenticate_response_names_permutation_and_note(tmp_path):
    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="right")
    responses.get(cronometer_auth.LOGIN_URL, body=LOGIN_PAGE_HTML)
    responses.post(
        cronometer_auth.LOGIN_URL,
        body=LOGIN_PAGE_HTML,
        headers={"Set-Cookie": "sesnonce=abc123; Path=/"},
    )
    responses.post(RPC_URL, body="//EX[some unexpected error shape]")

    with pytest.raises(CronometerAuthError) as exc_info:
        sign_in(config, creds)

    message = str(exc_info.value)
    assert config.gwt_permutation in message
    assert "2026-09-17-cronometer-endpoints.md" in message


@responses.activate
def test_unexpected_generate_token_response_names_permutation_and_note(tmp_path):
    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="right")
    responses.get(cronometer_auth.LOGIN_URL, body=LOGIN_PAGE_HTML)
    responses.post(
        cronometer_auth.LOGIN_URL,
        body=LOGIN_PAGE_HTML,
        headers={"Set-Cookie": "sesnonce=abc123; Path=/"},
    )
    responses.post(RPC_URL, body="//OK[12345678,1]")
    responses.post(RPC_URL, body="//EX[some unexpected error shape]")

    with pytest.raises(CronometerAuthError) as exc_info:
        sign_in(config, creds)

    message = str(exc_info.value)
    assert config.gwt_permutation in message
    assert "2026-09-17-cronometer-endpoints.md" in message


@responses.activate
def test_successful_sign_in_saves_session(tmp_path):
    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="right")
    responses.get(cronometer_auth.LOGIN_URL, body=LOGIN_PAGE_HTML)
    responses.post(
        cronometer_auth.LOGIN_URL,
        body=LOGIN_PAGE_HTML,
        headers={"Set-Cookie": "sesnonce=abc123; Path=/"},
    )
    responses.post(RPC_URL, body='//OK[12345678,1]')
    responses.post(RPC_URL, body='//OK["0123456789abcdef0123456789abcdef"]')

    session = sign_in(config, creds)

    assert session.cookies.get("sesnonce") == "abc123"
    assert config.session_path.exists()
    mode = stat.S_IMODE(config.session_path.stat().st_mode)
    assert mode == 0o600
    assert json.loads(config.session_path.read_text()) == {"sesnonce": "abc123"}


@responses.activate
def test_cached_session_reused_without_a_second_login_post(tmp_path):
    config = make_cronometer_config(tmp_path)
    config.session_path.parent.mkdir(parents=True, exist_ok=True)
    config.session_path.write_text(json.dumps({"sesnonce": "cached-nonce"}))
    # Only the RPC endpoint is registered; a call to LOGIN_URL would raise
    # ConnectionError from `responses`, proving no second login POST happens.
    responses.post(RPC_URL, body='//OK[12345678,1]')
    responses.post(RPC_URL, body='//OK["0123456789abcdef0123456789abcdef"]')

    session = cronometer_auth.load_cached_session(config.session_path)
    token = cronometer_auth.get_export_token(session, config)

    assert token == "0123456789abcdef0123456789abcdef"


@responses.activate
def test_generate_token_survives_duplicate_sesnonce_cookies(tmp_path):
    """Regression: live 2026-09-18 run hit CookieConflictError when Cronometer's
    authenticate RPC re-set `sesnonce` under a different domain than the one
    already in the jar, leaving two Cookie objects with the same name."""
    from requests.cookies import create_cookie

    config = make_cronometer_config(tmp_path)
    session = __import__("requests").Session()
    session.cookies.set_cookie(create_cookie(name="sesnonce", value="stale", domain="", expires=None))
    session.cookies.set_cookie(
        create_cookie(name="sesnonce", value="fresh", domain="cronometer.com", expires=9999999999)
    )
    responses.post(RPC_URL, body='//OK["0123456789abcdef0123456789abcdef"]')

    token = cronometer_auth._generate_export_token(session, config, user_id=12345678)

    assert token == "0123456789abcdef0123456789abcdef"
    sent_body = responses.calls[0].request.body
    assert "fresh" in sent_body and "stale" not in sent_body


def test_resolve_duplicate_cookies_prefers_longest_lived():
    from requests.cookies import create_cookie

    session = __import__("requests").Session()
    session.cookies.set_cookie(create_cookie(name="sesnonce", value="stale", domain="", expires=None))
    session.cookies.set_cookie(
        create_cookie(name="sesnonce", value="fresh", domain="cronometer.com", expires=9999999999)
    )

    resolved = cronometer_auth._resolve_duplicate_cookies(session.cookies)

    assert resolved["sesnonce"] == "fresh"


def test_load_cached_session_missing_file_returns_none(tmp_path):
    config = make_cronometer_config(tmp_path)
    assert cronometer_auth.load_cached_session(config.session_path) is None


def test_cli_cronometer_auth_prints_message_on_success(tmp_path, monkeypatch, capsys):
    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="right")
    monkeypatch.setattr(cli, "resolve_credentials", lambda cfg, interactive: creds)
    monkeypatch.setattr(cli, "sign_in", lambda cfg, creds: object())

    class Args:
        no_prompt = True

    code = cli.cmd_cronometer_auth(Args(), _wrap(config))
    assert code == 0
    assert "Signed in to Cronometer as me@example.com." in capsys.readouterr().out


def test_cli_cronometer_auth_persists_credentials_to_secrets_file(tmp_path, monkeypatch):
    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="right")
    monkeypatch.delenv("CRONOMETER_USERNAME", raising=False)
    monkeypatch.delenv("CRONOMETER_PASSWORD", raising=False)
    monkeypatch.setattr(cli, "resolve_credentials", lambda cfg, interactive: creds)
    monkeypatch.setattr(cli, "sign_in", lambda cfg, creds: object())

    class Args:
        no_prompt = True

    cli.cmd_cronometer_auth(Args(), _wrap(config))

    saved = cronometer_auth._load_saved_credentials(config.credentials_path)
    assert saved == creds


def test_cli_cronometer_auth_does_not_persist_env_var_credentials(tmp_path, monkeypatch):
    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="right")
    monkeypatch.setenv("CRONOMETER_USERNAME", "me@example.com")
    monkeypatch.setenv("CRONOMETER_PASSWORD", "right")
    monkeypatch.setattr(cli, "resolve_credentials", lambda cfg, interactive: creds)
    monkeypatch.setattr(cli, "sign_in", lambda cfg, creds: object())

    class Args:
        no_prompt = True

    cli.cmd_cronometer_auth(Args(), _wrap(config))

    assert not config.credentials_path.exists()


def test_cli_cronometer_auth_exits_2_on_auth_error(tmp_path, monkeypatch):
    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="wrong")
    monkeypatch.setattr(cli, "resolve_credentials", lambda cfg, interactive: creds)

    def raise_auth_error(cfg, creds):
        raise CronometerAuthError("nope")

    monkeypatch.setattr(cli, "sign_in", raise_auth_error)

    class Args:
        no_prompt = True

    code = cli.cmd_cronometer_auth(Args(), _wrap(config))
    assert code == 2


def _wrap(cronometer_config: CronometerConfig):
    """A minimal stand-in with just the `.cronometer` attribute cli.py reads."""

    class _Config:
        cronometer = cronometer_config

    return _Config()
