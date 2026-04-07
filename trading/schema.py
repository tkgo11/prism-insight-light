"""Trading signal parsing and validation."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


SUPPORTED_SIGNAL_TYPES = {"BUY", "SELL", "EVENT"}
SUPPORTED_MARKETS = {"KR", "US"}


class SignalValidationError(ValueError):
    """Raised when an inbound trading signal is malformed."""


def _as_float(value: Any, *, field_name: str) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise SignalValidationError(f"Invalid numeric value for '{field_name}'") from exc


def _as_int(value: Any, *, field_name: str) -> int | None:
    number = _as_float(value, field_name=field_name)
    return None if number is None else int(number)


@dataclass(slots=True)
class SignalMessage:
    """Validated inbound trading signal."""

    signal_type: str
    ticker: str = ""
    company_name: str = ""
    market: str = "KR"
    price: float | None = None
    target_price: float | None = None
    stop_loss: float | None = None
    buy_score: int | None = None
    rationale: str = ""
    profit_rate: float | None = None
    sell_reason: str = ""
    buy_price: float | None = None
    event_type: str = ""
    event_source: str = ""
    event_description: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_trade(self) -> bool:
        return self.signal_type in {"BUY", "SELL"}

    @property
    def is_event(self) -> bool:
        return self.signal_type == "EVENT"


def parse_signal_payload(payload: dict[str, Any]) -> SignalMessage:
    if not isinstance(payload, dict):
        raise SignalValidationError("Signal payload must be a JSON object")

    signal_type = str(payload.get("type", "")).strip().upper()
    if signal_type not in SUPPORTED_SIGNAL_TYPES:
        raise SignalValidationError(f"Unsupported signal type '{payload.get('type')}'")

    market = str(payload.get("market", "KR")).strip().upper() or "KR"
    if market not in SUPPORTED_MARKETS:
        raise SignalValidationError(f"Unsupported market '{payload.get('market')}'")

    ticker = str(payload.get("ticker", "")).strip().upper()
    company_name = str(payload.get("company_name", "")).strip()

    if signal_type in {"BUY", "SELL"} and not ticker:
        raise SignalValidationError("Trading signals require 'ticker'")

    price = _as_float(payload.get("price"), field_name="price")
    if signal_type in {"BUY", "SELL"} and price is None:
        raise SignalValidationError("Trading signals require 'price'")

    return SignalMessage(
        signal_type=signal_type,
        ticker=ticker,
        company_name=company_name or ticker,
        market=market,
        price=price,
        target_price=_as_float(payload.get("target_price"), field_name="target_price"),
        stop_loss=_as_float(payload.get("stop_loss"), field_name="stop_loss"),
        buy_score=_as_int(payload.get("buy_score"), field_name="buy_score"),
        rationale=str(payload.get("rationale", "")).strip(),
        profit_rate=_as_float(payload.get("profit_rate"), field_name="profit_rate"),
        sell_reason=str(payload.get("sell_reason", "")).strip(),
        buy_price=_as_float(payload.get("buy_price"), field_name="buy_price"),
        event_type=str(payload.get("event_type", "")).strip(),
        event_source=str(payload.get("source", "")).strip(),
        event_description=str(payload.get("event_description", "")).strip(),
        raw=dict(payload),
    )


def parse_signal_bytes(message_bytes: bytes) -> SignalMessage:
    try:
        payload = json.loads(message_bytes.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SignalValidationError("Signal payload must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise SignalValidationError("Signal payload must be valid JSON") from exc

    return parse_signal_payload(payload)


TradingSignal = SignalMessage
parse_signal = parse_signal_payload
