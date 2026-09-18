"""Cronometer sign-in for fitthing.

Credential resolution (Phase 1) has no network dependency. The
login -> sesnonce -> userId -> export-nonce flow (Phase 2) below is layered
on top of it, against the templates recorded in
agent-notes/2026-09-17-cronometer-endpoints.md. If Cronometer redeploys their
frontend, that recon note explains how to re-capture live requests; the
values to update are the module-level constants just below the imports.
"""

from __future__ import annotations

import getpass
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests

from fitthing.config import CronometerConfig

LOGIN_URL = "https://cronometer.com/login"
RPC_URL = "https://cronometer.com/cronometer/app"

# Both calls target the same service under the same GWT permutation, so they
# share this policy hash — see agent-notes/2026-09-17-cronometer-endpoints.md.
# Update these three alongside config.toml's gwt_permutation if Cronometer
# redeploys and exports start failing.
_SERVICE_POLICY_HASH = "13F6CC6C06AE73A6E95DE9B0233AB365"
_SERVICE_INTERFACE = "com.cronometer.shared.rpc.CronometerService"

_AUTHENTICATE_BODY = (
    "7|0|5|{module_base}|" + _SERVICE_POLICY_HASH + "|" + _SERVICE_INTERFACE + "|authenticate|"
    "java.lang.Integer/3438268394|1|2|3|4|1|5|5|{tz_offset_minutes}|"
)

_GENERATE_TOKEN_BODY = (
    "7|0|8|{module_base}|"
    + _SERVICE_POLICY_HASH
    + "|"
    + _SERVICE_INTERFACE
    + "|generateAuthorizationToken|java.lang.String/2004016611|I|"
    "com.cronometer.shared.user.AuthScope/2065601159|{sesnonce}|1|2|3|4|4|5|6|6|7|8|"
    "{user_id}|3600|7|2|"
)

_ANTICSRF_RE = re.compile(r'<input\b(?=[^>]*name="anticsrf")(?=[^>]*value="([^"]*)")[^>]*>')
_USER_ID_RE = re.compile(r"^//OK\[(\d+),")
_EXPORT_TOKEN_RE = re.compile(r'"([0-9a-f]{32})"')


class CronometerAuthError(Exception):
    """A Cronometer sign-in step failed in a way that needs a clear message."""


@dataclass(frozen=True)
class CronometerCredentials:
    username: str
    password: str


def save_credentials(path: Path, username: str, password: str) -> None:
    """Write credentials to `path` as JSON, chmod 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"username": username, "password": password}, indent=2))
    os.chmod(path, 0o600)


def _load_saved_credentials(path: Path) -> CronometerCredentials | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return CronometerCredentials(username=data["username"], password=data["password"])
    except (ValueError, OSError, KeyError):
        return None


def prompt_for_credentials() -> CronometerCredentials:
    """Ask for username/password interactively. Password is never echoed."""
    username = input("Cronometer username (email): ").strip()
    password = getpass.getpass("Cronometer password: ")
    return CronometerCredentials(username=username, password=password)


def resolve_credentials(config: CronometerConfig, *, interactive: bool) -> CronometerCredentials:
    """Resolve credentials: env vars, then the secrets file, then an interactive prompt.

    Matches the precedence in agent-notes/2026-09-17-cronometer-nutrition-plan.md
    §3.2. Exits with code 2, mirroring `fitthing.auth.ensure_signed_in`'s
    contract, if nothing is available and prompting isn't allowed.
    """
    env_username = os.environ.get("CRONOMETER_USERNAME")
    env_password = os.environ.get("CRONOMETER_PASSWORD")
    if env_username and env_password:
        return CronometerCredentials(username=env_username, password=env_password)

    saved = _load_saved_credentials(config.credentials_path)
    if saved is not None:
        return saved

    if interactive:
        return prompt_for_credentials()

    print("Cronometer sign-in required. Run: fitthing cronometer-auth", file=sys.stderr)
    sys.exit(2)


def _local_timezone_offset_minutes() -> int:
    """UTC offset in minutes, sign matching what Cronometer's client sends live
    (observed 2026-09-18: -300 for EST/UTC-5)."""
    offset = datetime.now().astimezone().utcoffset()
    return int(offset.total_seconds() // 60) if offset is not None else 0


def _rpc_headers(config: CronometerConfig) -> dict[str, str]:
    return {
        "Content-Type": "text/x-gwt-rpc; charset=utf-8",
        "X-GWT-Module-Base": config.gwt_module_base,
        "X-GWT-Permutation": config.gwt_permutation,
    }


def _redeploy_hint(gwt_permutation: str, response_text: str) -> str:
    return (
        f"(gwt_permutation={gwt_permutation!r}). Got: {response_text[:200]!r}. "
        "Cronometer's frontend may have redeployed — redo the Phase 0 recon in "
        "agent-notes/2026-09-17-cronometer-endpoints.md and update "
        "cronometer/auth.py's request templates."
    )


def _extract_anticsrf(html: str) -> str:
    match = _ANTICSRF_RE.search(html)
    if match is None:
        raise CronometerAuthError(
            "Could not find the anticsrf token on the Cronometer login page. "
            "The login page may have changed structure — redo the Phase 0 recon "
            "in agent-notes/2026-09-17-cronometer-endpoints.md."
        )
    return match.group(1)


def _login(session: requests.Session, config: CronometerConfig, creds: CronometerCredentials) -> None:
    """POST the login form. Raises CronometerAuthError if no session cookie results."""
    login_page = session.get(LOGIN_URL, timeout=30)
    login_page.raise_for_status()
    anticsrf = _extract_anticsrf(login_page.text)

    response = session.post(
        LOGIN_URL,
        data={
            "username": creds.username,
            "password": creds.password,
            "userCode": "",
            "anticsrf": anticsrf,
        },
        timeout=30,
    )
    response.raise_for_status()

    if "sesnonce" not in session.cookies.get_dict():
        raise CronometerAuthError(
            "Cronometer login did not create a session. If this account has 2FA "
            "enabled, scripted login will not work — use `fitthing "
            "cronometer-import <file.csv>` instead. Otherwise, double-check the "
            "username and password."
        )


def _authenticate(session: requests.Session, config: CronometerConfig) -> int:
    """The post-login GWT-RPC call. Returns the numeric Cronometer user id."""
    body = _AUTHENTICATE_BODY.format(
        module_base=config.gwt_module_base,
        tz_offset_minutes=_local_timezone_offset_minutes(),
    )
    response = session.post(RPC_URL, data=body, headers=_rpc_headers(config), timeout=30)
    response.raise_for_status()

    match = _USER_ID_RE.match(response.text)
    if match is None:
        raise CronometerAuthError(
            "Unexpected response from Cronometer's authenticate call "
            + _redeploy_hint(config.gwt_permutation, response.text)
        )
    return int(match.group(1))


def _generate_export_token(session: requests.Session, config: CronometerConfig, user_id: int) -> str:
    """The generateAuthorizationToken GWT-RPC call. Returns the export nonce."""
    sesnonce = session.cookies.get("sesnonce")
    body = _GENERATE_TOKEN_BODY.format(
        module_base=config.gwt_module_base,
        sesnonce=sesnonce,
        user_id=user_id,
    )
    response = session.post(RPC_URL, data=body, headers=_rpc_headers(config), timeout=30)
    response.raise_for_status()

    match = _EXPORT_TOKEN_RE.search(response.text)
    if match is None:
        raise CronometerAuthError(
            "Unexpected response from Cronometer's generateAuthorizationToken call "
            + _redeploy_hint(config.gwt_permutation, response.text)
        )
    return match.group(1)


def get_export_token(session: requests.Session, config: CronometerConfig) -> str:
    """Mint a short-lived (1hr) export token from an existing, signed-in session.

    Used both to verify a fresh sign-in worked and, later (client.py, Phase 3),
    on every export run — the token is too short-lived to cache, unlike the
    `sesnonce` session cookie.
    """
    user_id = _authenticate(session, config)
    return _generate_export_token(session, config, user_id)


def save_session(path: Path, session: requests.Session) -> None:
    """Persist session cookies to `path` as JSON, chmod 0600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(session.cookies.get_dict()))
    os.chmod(path, 0o600)


def load_cached_session(path: Path) -> requests.Session | None:
    """Load a previously saved session, or None if there isn't one / it's unreadable."""
    if not path.exists():
        return None
    try:
        cookies = json.loads(path.read_text())
    except (ValueError, OSError):
        return None
    session = requests.Session()
    session.cookies.update(cookies)
    return session


def sign_in(config: CronometerConfig, creds: CronometerCredentials) -> requests.Session:
    """Log in fresh, verify it worked by minting an export token, and cache the session.

    Raises CronometerAuthError on any failure (bad credentials, 2FA, or an
    unexpected response shape indicating Cronometer redeployed).
    """
    session = requests.Session()
    _login(session, config, creds)
    get_export_token(session, config)  # verifies the new session actually works
    save_session(config.session_path, session)
    return session
