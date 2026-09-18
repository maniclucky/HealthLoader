from datetime import date
from pathlib import Path

import pytest
import responses

from fitthing.config import CronometerConfig
from fitthing.cronometer.auth import CronometerCredentials
from fitthing.cronometer.client import (
    CronometerClient,
    CronometerClientError,
    EXPORT_URL,
    ExportCapReached,
)


def make_cronometer_config(tmp_path: Path) -> CronometerConfig:
    return CronometerConfig(
        enabled=True,
        credentials_path=tmp_path / "cronometer.json",
        session_path=tmp_path / "cronometer_session.json",
        start_date=None,
        gwt_module_base="https://cronometer.com/cronometer/",
        gwt_permutation="deadbeef",
        request_delay_seconds=0.0,
    )


CSV_BODY = "Date,Energy (kcal),Protein (g),Carbs (g),Fat (g)\n2025-01-01,2000,100,200,60\n"
LOGIN_PAGE_HTML = '<html><body><input value="tok-abc123" name="anticsrf"></body></html>'


def _stub_get_export_token(monkeypatch, token="19317289bc1210cdf92d3e5312058ac5"):
    import fitthing.cronometer.client as client_module

    monkeypatch.setattr(client_module, "get_export_token", lambda session, config: token)


@responses.activate
def test_export_with_existing_session_no_login(tmp_path, monkeypatch):
    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="pw")
    _stub_get_export_token(monkeypatch)
    responses.get(EXPORT_URL, body=CSV_BODY)

    import requests

    client = CronometerClient(config, creds, session=requests.Session())
    result = client.export_daily_summary(date(2025, 1, 1), date(2025, 1, 1))

    assert result == CSV_BODY


@responses.activate
def test_export_cap_reached_raises_and_does_not_retry(tmp_path, monkeypatch):
    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="pw")
    _stub_get_export_token(monkeypatch)
    responses.get(EXPORT_URL, body="You have reached your daily export limit.")

    import requests

    client = CronometerClient(config, creds, session=requests.Session())

    with pytest.raises(ExportCapReached):
        client.export_daily_summary(date(2025, 1, 1), date(2025, 1, 1))

    assert len(responses.calls) == 1


@responses.activate
def test_expired_session_triggers_one_relogin_then_succeeds(tmp_path, monkeypatch):
    import fitthing.cronometer.client as client_module
    import requests

    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="pw")

    _stub_get_export_token(monkeypatch)

    fresh_session = requests.Session()
    fresh_session.cookies.set("sesnonce", "fresh")
    monkeypatch.setattr(client_module, "sign_in", lambda cfg, creds: fresh_session)

    responses.get(EXPORT_URL, body=LOGIN_PAGE_HTML)  # stale cached session
    responses.get(EXPORT_URL, body=CSV_BODY)  # after fresh login

    client = CronometerClient(config, creds, session=requests.Session())
    result = client.export_daily_summary(date(2025, 1, 1), date(2025, 1, 1))

    assert result == CSV_BODY
    assert len(responses.calls) == 2


@responses.activate
def test_non_retryable_http_error_raises(tmp_path, monkeypatch):
    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="pw")
    _stub_get_export_token(monkeypatch)
    responses.get(EXPORT_URL, status=404, body="not found")

    import requests

    client = CronometerClient(config, creds, session=requests.Session())

    with pytest.raises(CronometerClientError):
        client.export_daily_summary(date(2025, 1, 1), date(2025, 1, 1))


@responses.activate
def test_retries_transient_5xx_then_succeeds(tmp_path, monkeypatch):
    config = make_cronometer_config(tmp_path)
    creds = CronometerCredentials(username="me@example.com", password="pw")
    _stub_get_export_token(monkeypatch)
    responses.get(EXPORT_URL, status=503, body="unavailable")
    responses.get(EXPORT_URL, body=CSV_BODY)

    import requests

    client = CronometerClient(
        config, creds, session=requests.Session(), sleep=lambda seconds: None
    )
    result = client.export_daily_summary(date(2025, 1, 1), date(2025, 1, 1))

    assert result == CSV_BODY
    assert len(responses.calls) == 2
