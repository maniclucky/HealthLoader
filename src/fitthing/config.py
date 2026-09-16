"""Load fitthing's config.toml (falling back to config.example.toml's defaults)."""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("config.toml")

DEFAULTS = {
    "client_secret_path": "secrets/client_secret.json",
    "token_path": "secrets/token.json",
    "output_dir": "data",
    "start_date": None,
    "data_types": "all",
    "window_days": 14,
}


@dataclass(frozen=True)
class Config:
    client_secret_path: Path
    token_path: Path
    output_dir: Path
    start_date: str | None
    data_types: str | list[str]
    window_days: int

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
    )
