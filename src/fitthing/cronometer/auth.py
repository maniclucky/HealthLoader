"""Cronometer sign-in for fitthing.

Credential resolution (this file, Phase 1) has no network dependency. The
login -> sesnonce -> userId -> export-nonce flow (Phase 2) is layered on top
of it, against the templates recorded in
agent-notes/2026-09-17-cronometer-endpoints.md.
"""

from __future__ import annotations

import getpass
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from fitthing.config import CronometerConfig


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
