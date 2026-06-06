"""Signal validation adapters that reuse the existing trading schema."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any


def _signal_to_dto(signal) -> dict[str, Any]:
    payload = asdict(signal)
    payload.pop("raw", None)
    payload["is_trade"] = signal.is_trade
    payload["is_event"] = signal.is_event
    return payload


def parse_signal_input(payload: dict[str, Any] | str) -> dict[str, Any]:
    from trading.schema import SignalValidationError, parse_signal_payload

    try:
        if isinstance(payload, str):
            parsed_payload = json.loads(payload)
        else:
            parsed_payload = payload
        if not isinstance(parsed_payload, dict):
            raise SignalValidationError("Signal payload must be a JSON object")
        signal = parse_signal_payload(parsed_payload)
        return {"ok": True, "signal": _signal_to_dto(signal), "error": None}
    except (json.JSONDecodeError, SignalValidationError, TypeError, ValueError) as exc:
        return {"ok": False, "signal": None, "error": str(exc)}


def parse_signal_text(text: str) -> dict[str, Any]:
    """Parse JSON first, then fall back to the existing Telegram labeled-text parser."""
    json_result = parse_signal_input(text)
    if json_result["ok"]:
        return json_result

    try:
        from trading.telegram_fetch import parse_signal_text as parse_telegram_text

        payload = parse_telegram_text(text)
        if payload is None:
            return json_result
        return parse_signal_input(payload)
    except Exception as exc:  # noqa: BLE001 - safe UI error surface
        return {"ok": False, "signal": None, "error": str(exc)}
