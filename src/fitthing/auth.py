"""Google OAuth sign-in for fitthing.

`ensure_signed_in()` is the single entry point every API-using command calls
before doing anything else. See Phase 2 of
agent-notes/2026-09-16-google-health-export-plan.md for the exact behavior
this implements.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as AuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from fitthing.config import Config

# Single source of truth for the scopes this tool ever requests. datatypes.py
# (Phase 3) builds each data type's required scope from SCOPE_PREFIX so the
# two never drift apart.
SCOPE_PREFIX = "https://www.googleapis.com/auth/googlehealth."
SCOPE_SUFFIXES = (
    "activity_and_fitness",
    "health_metrics_and_measurements",
    "sleep",
    "nutrition",
    "ecg",
    "irn",
)
ALL_SCOPES = [f"{SCOPE_PREFIX}{suffix}.readonly" for suffix in SCOPE_SUFFIXES]

IDENTITY_URL = "https://health.googleapis.com/v4/users/me/identity"

# Testing-mode refresh tokens last 7 days; start warning with 1 day left.
TOKEN_LIFETIME_DAYS = 7
REAUTH_WARNING_AGE_DAYS = 6


def _client_secret_missing_exit(config: Config) -> None:
    print(
        f"No OAuth client file at {config.client_secret_path}.\n"
        "Create a Desktop app OAuth client in Google Cloud Console and "
        f"download it to {config.client_secret_path}.",
        file=sys.stderr,
    )
    sys.exit(2)


def _read_meta(meta_path: Path) -> dict | None:
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text())
    except (ValueError, OSError):
        return None


def _write_meta(meta_path: Path, scopes: list[str]) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "authorized_at": datetime.now(timezone.utc).isoformat(),
        "scopes": scopes,
    }
    meta_path.write_text(json.dumps(meta, indent=2))
    os.chmod(meta_path, 0o600)


def _load_token(token_path: Path, required_scopes: list[str]) -> Credentials | None:
    """Load a saved token if it exists and covers `required_scopes`."""
    if not token_path.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(token_path))
    except (ValueError, OSError):
        return None
    if not set(required_scopes).issubset(set(creds.scopes or [])):
        return None
    return creds


def _save_token(token_path: Path, creds: Credentials) -> None:
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(creds.to_json())
    os.chmod(token_path, 0o600)


def _check_identity(creds: Credentials) -> bool:
    """Cheap live check. Returns False only on 401 (sign-in no longer valid).

    Any other status — including a real API error such as the account not
    being linked to Google Health — says nothing about whether the token
    itself is still good, so it's left for the caller's own request to
    surface. Re-authorizing would not fix a non-auth error anyway.
    """
    resp = requests.get(
        IDENTITY_URL,
        headers={"Authorization": f"Bearer {creds.token}"},
        timeout=30,
    )
    return resp.status_code != 401


def run_browser_flow(config: Config, scopes: list[str]) -> Credentials:
    """Run the interactive OAuth flow and persist the resulting credentials.

    Used directly by `fitthing auth` (to sign in or renew on purpose) and by
    `ensure_signed_in` when a fresh sign-in turns out to be necessary.
    """
    if not config.client_secret_path.exists():
        _client_secret_missing_exit(config)

    flow = InstalledAppFlow.from_client_secrets_file(str(config.client_secret_path), scopes=scopes)
    print("Opening a browser to sign in with Google...")
    print("If nothing opens, use the URL printed below to continue manually.")
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        open_browser=True,
    )

    _save_token(config.token_path, creds)
    _write_meta(config.token_meta_path, scopes)
    print(f"Signed in. Token saved to {config.token_path}.")
    return creds


def _confirm_reauth_or_exit(reason: str, interactive: bool) -> None:
    """Ask to sign in again; exit(2) if declined, or immediately if not interactive."""
    if not interactive:
        print("Sign-in required. Run: fitthing auth", file=sys.stderr)
        sys.exit(2)
    print(reason)
    answer = input("Sign in now? A browser window will open. [Y/n]: ").strip().lower()
    if answer not in ("", "y", "yes"):
        sys.exit(2)


def ensure_signed_in(
    config: Config,
    scopes: list[str] | None = None,
    interactive: bool | None = None,
) -> Credentials:
    """Return valid, scope-covering Credentials, signing in again if necessary.

    `interactive` defaults to whether stdin is a TTY. Callers wire a
    `--no-prompt` flag to force False so this never blocks waiting for input.
    """
    scopes = scopes if scopes is not None else ALL_SCOPES
    if interactive is None:
        interactive = sys.stdin.isatty()

    # Without a client secret file there is no way to (re)authorize at all,
    # so this is a hard, non-interactive failure regardless of token state.
    if not config.client_secret_path.exists():
        _client_secret_missing_exit(config)

    creds = _load_token(config.token_path, scopes)
    had_saved_token = creds is not None

    if creds is not None:
        try:
            creds.refresh(AuthRequest())
        except RefreshError:
            creds = None

    if creds is not None and not _check_identity(creds):
        creds = None

    if creds is None:
        reason = (
            "Your Google sign-in has expired. Testing-mode sign-ins last 7 days."
            if had_saved_token or config.token_path.exists()
            else "You haven't signed in with Google yet."
        )
        _confirm_reauth_or_exit(reason, interactive)
        return run_browser_flow(config, scopes)

    meta = _read_meta(config.token_meta_path)
    if meta is not None:
        authorized_at = datetime.fromisoformat(meta["authorized_at"])
        age_days = (datetime.now(timezone.utc) - authorized_at).days
        if age_days >= REAUTH_WARNING_AGE_DAYS:
            print(
                "Google sign-in expires in about 1 day. Run `fitthing auth` to renew.",
                file=sys.stderr,
            )

    return creds
