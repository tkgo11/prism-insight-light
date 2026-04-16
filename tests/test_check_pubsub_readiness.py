from __future__ import annotations

import importlib
import sys
import types
from typing import Any

import pytest


MODULE_NAME = "check_pubsub_readiness"


class FakeResult:
    def __init__(self, *, status: str, exit_code: int, message: str, diagnostics: list[str] | None = None):
        self.status = status
        self.exit_code = exit_code
        self.message = message
        self.diagnostics = diagnostics or []


def _reset_module(name: str) -> None:
    sys.modules.pop(name, None)


def _make_pubsub_module(result: FakeResult):
    module = types.ModuleType("pubsub_readiness")
    module.ENV_PROJECT_ID = "GCP_PROJECT_ID"
    module.ENV_SUBSCRIPTION_ID = "GCP_PUBSUB_SUBSCRIPTION_ID"
    module.ENV_CREDENTIALS_PATH = "GCP_CREDENTIALS_PATH"

    def runner(**kwargs: Any):
        return result

    module.check_pubsub_readiness = runner
    return module


def _import_cli(monkeypatch: pytest.MonkeyPatch, *, result: FakeResult):
    before_modules = set(sys.modules)
    _reset_module(MODULE_NAME)
    _reset_module("pubsub_readiness")
    monkeypatch.setitem(sys.modules, "pubsub_readiness", _make_pubsub_module(result))
    importlib.invalidate_caches()
    module = importlib.import_module(MODULE_NAME)
    imported_now = set(sys.modules) - before_modules
    assert "subscriber" not in imported_now
    assert not any(name == "trading" or name.startswith("trading.") for name in imported_now)
    return module


def _call_main(module, argv):
    main = getattr(module, "main", None)
    assert callable(main), "check_pubsub_readiness.main is required"
    return main(argv)


def test_cli_returns_zero_and_prints_ready_message(monkeypatch, capsys):
    module = _import_cli(
        monkeypatch,
        result=FakeResult(
            status="ready",
            exit_code=0,
            message="READY: consume permission confirmed for projects/demo/subscriptions/sub",
            diagnostics=["subscription metadata unavailable"],
        ),
    )

    exit_code = _call_main(module, ["--project-id", "demo", "--subscription-id", "sub"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "READY:" in captured.out
    assert "metadata unavailable" in captured.out
    assert captured.err == ""


def test_cli_returns_denied_exit_code_and_prints_not_ready_message(monkeypatch, capsys):
    module = _import_cli(
        monkeypatch,
        result=FakeResult(
            status="denied",
            exit_code=2,
            message="NOT READY: missing pubsub.subscriptions.consume on projects/demo/subscriptions/sub",
        ),
    )

    exit_code = _call_main(module, ["--project-id", "demo", "--subscription-id", "sub"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert captured.out == ""
    assert "NOT READY:" in captured.err
    assert "pubsub.subscriptions.consume" in captured.err


def test_cli_returns_hard_failure_exit_code_for_missing_config(monkeypatch, capsys):
    module = _import_cli(
        monkeypatch,
        result=FakeResult(
            status="failure",
            exit_code=1,
            message="NOT READY: missing GCP_PROJECT_ID and GCP_PUBSUB_SUBSCRIPTION_ID",
        ),
    )

    exit_code = _call_main(module, [])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.out == ""
    assert "NOT READY:" in captured.err
    assert "GCP_PROJECT_ID" in captured.err
