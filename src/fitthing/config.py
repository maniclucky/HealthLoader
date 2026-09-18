"""Load fitthing's config.toml (falling back to config.example.toml's defaults)."""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config.toml")

DEFAULTS = {
    "client_secret_path": "secrets/client_secret.json",
    "token_path": "secrets/token.json",
    "output_dir": "data",
    "start_date": None,
    "data_types": "all",
    "window_days": 14,
    "request_delay_seconds": 0.5,
}

# Unofficial endpoints — see agent-notes/2026-09-17-cronometer-nutrition-plan.md and
# agent-notes/2026-09-17-cronometer-endpoints.md. gwt_module_base/gwt_permutation are
# copied from a live browser session and break whenever Cronometer redeploys.
CRONOMETER_DEFAULTS = {
    "enabled": False,
    "credentials_path": "secrets/cronometer.json",
    "session_path": "secrets/cronometer_session.json",
    "start_date": None,
    "gwt_module_base": "https://cronometer.com/cronometer/",
    "gwt_permutation": "",
    "request_delay_seconds": 1.0,
}


@dataclass(frozen=True)
class CronometerConfig:
    enabled: bool
    credentials_path: Path
    session_path: Path
    start_date: str | None
    gwt_module_base: str
    gwt_permutation: str
    request_delay_seconds: float


def _load_cronometer_config(raw: dict) -> CronometerConfig:
    values = {**CRONOMETER_DEFAULTS, **raw}
    return CronometerConfig(
        enabled=bool(values["enabled"]),
        credentials_path=Path(values["credentials_path"]),
        session_path=Path(values["session_path"]),
        start_date=values["start_date"],
        gwt_module_base=values["gwt_module_base"],
        gwt_permutation=values["gwt_permutation"],
        request_delay_seconds=float(values["request_delay_seconds"]),
    )


@dataclass(frozen=True)
class Config:
    client_secret_path: Path
    token_path: Path
    output_dir: Path
    start_date: str | None
    data_types: str | list[str]
    window_days: int
    request_delay_seconds: float
    cronometer: CronometerConfig = field(default_factory=lambda: _load_cronometer_config({}))

    @property
    def token_meta_path(self) -> Path:
        return self.token_path.with_name("token_meta.json")

    @property
    def state_path(self) -> Path:
        return self.output_dir / "state.json"

    @property
    def raw_dir(self) -> Path:
        return self.output_dir / "raw"


def load_config(path: Path | None = None) -> Config:
    """Load config.toml, filling in any missing keys with DEFAULTS.

    Exits with a clear message (and code 2) if config.toml is missing so the
    user knows to copy config.example.toml first, unless defaults alone are
    workable for the command being run (callers may choose to tolerate a
    missing file for read-only commands like `--help`).
    """
    path = path or DEFAULT_CONFIG_PATH
    values = dict(DEFAULTS)

    if path.exists():
        with path.open("rb") as f:
            values.update(tomllib.load(f))
    else:
        print(
            f"No {path} found. Copy config.example.toml to {path} and edit it.",
            file=sys.stderr,
        )

    return Config(
        client_secret_path=Path(values["client_secret_path"]),
        token_path=Path(values["token_path"]),
        output_dir=Path(values["output_dir"]),
        start_date=values["start_date"],
        data_types=values["data_types"],
        window_days=int(values["window_days"]),
        request_delay_seconds=float(values["request_delay_seconds"]),
        cronometer=_load_cronometer_config(values.get("cronometer", {})),
    )
