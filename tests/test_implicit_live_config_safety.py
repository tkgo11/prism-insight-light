"""Fail-closed tests for implicit example KIS configuration (no broker calls).

An example containing default_mode=real must never silently arm production
trading when the operator has not provided a live configuration.
"""
from __future__ import annotations

import pytest

from trading import config_paths, market_hours
from trading.domestic import DomesticStockTrading
from trading.us import USStockTrading


def test_implicit_example_cannot_select_real_mode(monkeypatch, tmp_path):
    example = tmp_path / "kis_devlp.yaml.example"
    example.write_text("default_mode: real\nauto_trading: true\n", encoding="utf-8")
    monkeypatch.delenv(config_paths.KIS_CONFIG_PATH_ENV, raising=False)
    monkeypatch.setattr(config_paths, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    monkeypatch.setattr(config_paths, "EXAMPLE_CONFIG_PATH", example)
    assert config_paths.active_kis_config_path() == example
    assert market_hours.get_trading_mode() == "demo"


def test_explicit_missing_config_does_not_silently_arm_real(monkeypatch, tmp_path):
    missing = tmp_path / "explicit-missing.yaml"
    monkeypatch.setenv(config_paths.KIS_CONFIG_PATH_ENV, str(missing))
    with pytest.raises(RuntimeError, match="KIS config"):
        market_hours.get_trading_mode()


def test_explicit_config_without_mode_defaults_to_demo(monkeypatch, tmp_path):
    config = tmp_path / "explicit.yaml"
    config.write_text("auto_trading: true\n", encoding="utf-8")
    monkeypatch.setenv(config_paths.KIS_CONFIG_PATH_ENV, str(config))
    assert market_hours.get_trading_mode() == "demo"


def test_explicit_real_mode_remains_operator_controlled(monkeypatch, tmp_path):
    config = tmp_path / "explicit.yaml"
    config.write_text("default_mode: real\nauto_trading: true\n", encoding="utf-8")
    monkeypatch.setenv(config_paths.KIS_CONFIG_PATH_ENV, str(config))
    assert market_hours.get_trading_mode() == "real"


def test_implicit_example_cannot_set_direct_broker_real_defaults(monkeypatch):
    monkeypatch.delenv(config_paths.KIS_CONFIG_PATH_ENV, raising=False)
    if config_paths.DEFAULT_CONFIG_PATH.exists():
        pytest.skip("A real local KIS config exists; fallback not in use")
    assert DomesticStockTrading.DEFAULT_MODE == "demo"
    assert USStockTrading.DEFAULT_MODE == "demo"


def test_shipped_example_never_defaults_to_real():
    import yaml

    example = config_paths.EXAMPLE_CONFIG_PATH
    config = yaml.safe_load(example.read_text(encoding="utf-8"))
    assert config["default_mode"] == "demo"


def test_direct_broker_default_without_live_config_is_not_real(tmp_path):
    import os
    import subprocess
    import sys

    # Separate interpreter: module-level DEFAULT_MODE is evaluated at import,
    # and reloading KIS modules in the test process would contaminate fixtures.
    script = (
        "from pathlib import Path\n"
        "from trading import config_paths\n"
        f"config_paths.DEFAULT_CONFIG_PATH = Path({str(tmp_path / 'missing.yaml')!r})\n"
        "from trading.domestic import DomesticStockTrading\n"
        "from trading.us import USStockTrading\n"
        "print(DomesticStockTrading.DEFAULT_MODE, USStockTrading.DEFAULT_MODE)\n"
    )
    environment = dict(os.environ)
    environment.pop(config_paths.KIS_CONFIG_PATH_ENV, None)
    result = subprocess.run(
        [sys.executable, "-c", script], env=environment,
        text=True, capture_output=True, check=True, timeout=30,
    )
    assert result.stdout.strip() == "demo demo"
