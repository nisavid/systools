"""Resolve filesystem locations from the deployment contract."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimePaths:
    config_dir: Path
    state_dir: Path
    log_dir: Path

    @property
    def config_file(self) -> Path:
        return self.config_dir / "config.toml"


def resolve_paths(
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
) -> RuntimePaths:
    """Return effective mlxd directories without creating them."""
    values = os.environ if environ is None else environ
    home_dir = Path.home() if home is None else home
    return RuntimePaths(
        config_dir=_directory(values, "MLXD_CONFIG_DIR", home_dir / ".config/mlxd"),
        state_dir=_directory(values, "MLXD_STATE_DIR", home_dir / ".local/state/mlxd"),
        log_dir=_directory(values, "MLXD_LOG_DIR", home_dir / "Library/Logs/mlxd"),
    )


def _directory(values: Mapping[str, str], key: str, default: Path) -> Path:
    value = values.get(key)
    return Path(value).expanduser() if value else default
