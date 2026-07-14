"""Central KIS configuration path resolution.

Runtime code reads one explicit override when configured and otherwise keeps
the historical live-file-then-example fallback.  Tests and embedded tools can
therefore use an isolated config without writing into the source tree.
"""

from __future__ import annotations

import os
from pathlib import Path


KIS_CONFIG_PATH_ENV = "PRISM_KIS_CONFIG_PATH"
TRADING_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = TRADING_DIR / "config" / "kis_devlp.yaml"
EXAMPLE_CONFIG_PATH = TRADING_DIR / "config" / "kis_devlp.yaml.example"


def configured_kis_config_path(env: dict[str, str] | None = None) -> Path | None:
    source = os.environ if env is None else env
    raw_path = str(source.get(KIS_CONFIG_PATH_ENV) or "").strip()
    return Path(raw_path).expanduser() if raw_path else None


def writable_kis_config_path(env: dict[str, str] | None = None) -> Path:
    """Return the path where operator edits should be persisted."""

    return configured_kis_config_path(env) or DEFAULT_CONFIG_PATH


def active_kis_config_path(env: dict[str, str] | None = None) -> Path:
    """Return the explicit/live config, or the maintained safe example."""

    configured = configured_kis_config_path(env)
    if configured is not None:
        return configured
    if DEFAULT_CONFIG_PATH.exists():
        return DEFAULT_CONFIG_PATH
    return EXAMPLE_CONFIG_PATH
