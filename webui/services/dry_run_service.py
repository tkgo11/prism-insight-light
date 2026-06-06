"""Structurally isolated dry-run simulator.

This service intentionally does not instantiate TradeDispatcher, KIS clients, or the
queue. It mirrors DispatchResult shape for operator feedback only.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .signal_service import parse_signal_input


@dataclass(frozen=True)
class DryRunResult:
    status: str
    message: str
    signal_type: str
    market: str
    ticker: str
    company_name: str


def simulate_dispatch(payload: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_signal_input(payload)
    if not parsed["ok"]:
        return {"ok": False, "result": None, "error": parsed["error"]}

    signal = parsed["signal"]
    result = DryRunResult(
        status="dry-run",
        message="Dry-run mode; no trade executed and no queue mutation performed",
        signal_type=signal["signal_type"],
        market=signal["market"],
        ticker=signal["ticker"],
        company_name=signal["company_name"],
    )
    return {"ok": True, "result": asdict(result), "signal": signal, "error": None}
