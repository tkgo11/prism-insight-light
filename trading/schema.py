"""Trading signal parsing and validation."""

from __future__ import annotations

import json
import math
from decimal import Decimal, InvalidOperation
from dataclasses import dataclass, field
from typing import Any


SUPPORTED_SIGNAL_TYPES = {"BUY", "SELL", "EVENT"}
SUPPORTED_MARKETS = {"KR", "US"}


def infer_market(ticker: str) -> str:
    """Infer market from ticker shape when upstream payload omits it."""

    stripped = ticker.strip()
    return "KR" if stripped.isascii() and stripped.isdigit() else "US"


class SignalValidationError(ValueError):
    """Raised when an inbound trading signal is malformed."""


def reject_duplicate_signal_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Fail closed on ambiguous JSON objects, including nested objects.

    Python's default JSON decoder keeps the final occurrence of a duplicate
    key. For trading signals this can silently override the publisher's
    requested ticker, order size, or exit denominator.
    """
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SignalValidationError("Duplicate JSON key in trading signal")
        result[key] = value
    return result


def _as_text(value: Any, *, field_name: str) -> str:
    """Coerce an optional text field; reject non-string payloads and log-hostile chars."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SignalValidationError(f"'{field_name}' must be a string")
    cleaned = "".join(ch if ch.isprintable() else " " for ch in value)
    return " ".join(cleaned.split())


def _as_float(value: Any, *, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise SignalValidationError(f"Invalid numeric value for '{field_name}'")
    if isinstance(value, str) and not value.strip():
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        # JSON may contain arbitrarily large integer literals.  Converting
        # one to float raises OverflowError; keep malformed publisher input
        # inside the normal SignalValidationError/ACK-NACK safety path.
        raise SignalValidationError(f"Invalid numeric value for '{field_name}'") from exc
    if not math.isfinite(number):
        raise SignalValidationError(f"Invalid numeric value for '{field_name}'")
    return number


# All integer-valued order fields must survive JSON/string-to-number conversion
# exactly. IEEE-754 doubles cannot represent every integer at or above 2**53.
_MAX_EXACT_SIGNAL_INTEGER = (1 << 53) - 1


def _as_int(value: Any, *, field_name: str) -> int | None:
    error = f"Invalid integer value for '{field_name}'"
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise SignalValidationError(error)
    if isinstance(value, int):
        if abs(value) > _MAX_EXACT_SIGNAL_INTEGER:
            raise SignalValidationError(error)
        return value
    if isinstance(value, (str, Decimal)):
        # float("8.0000000000000001") == 8.0, which would silently turn
        # a fractional denominator/score into an integer. Parse strings
        # exactly, including exponent notation, before converting to int.
        try:
            exact = Decimal(value.strip() if isinstance(value, str) else value)
        except (InvalidOperation, ValueError, TypeError) as exc:
            raise SignalValidationError(error) from exc
        if (
            not exact.is_finite()
            or abs(exact) > _MAX_EXACT_SIGNAL_INTEGER
            or exact != exact.to_integral_value()
        ):
            raise SignalValidationError(error)
        return int(exact)
    number = _as_float(value, field_name=field_name)
    if number is None:
        return None
    if abs(number) > _MAX_EXACT_SIGNAL_INTEGER or not number.is_integer():
        raise SignalValidationError(error)
    return int(number)


def _as_positive_float(value: Any, *, field_name: str) -> float | None:
    number = _as_float(value, field_name=field_name)
    if number is not None and number <= 0:
        raise SignalValidationError(f"'{field_name}' must be greater than 0")
    return number


def load_signal_json(text: str | bytes) -> Any:
    """Decode signal JSON without losing integer-field precision.

    json.loads normally rounds 8.0000000000000001 to 8.0 *before* the
    integer validator sees it. Inspect Decimal tokens first, then return
    ordinary JSON types so raw signals remain queue/ledger serializable.
    Duplicate keys are rejected during both passes, including nested data.
    """
    try:
        exact = json.loads(
            text, object_pairs_hook=reject_duplicate_signal_keys, parse_float=Decimal
        )
    except InvalidOperation as exc:
        raise SignalValidationError("Invalid JSON numeric literal") from exc

    def check_integer_tokens(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"buy_score", "sell_denominator"} and isinstance(item, Decimal):
                    _as_int(item, field_name=key)
                check_integer_tokens(item)
        elif isinstance(value, list):
            for item in value:
                check_integer_tokens(item)

    check_integer_tokens(exact)
    return json.loads(text, object_pairs_hook=reject_duplicate_signal_keys)


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
    position_fraction: float = 1.0
    sell_denominator: int = 1
    event_type: str = ""
    event_source: str = ""
    event_description: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
    sell_fraction: float | None = None

    @property
    def is_trade(self) -> bool:
        return self.signal_type in {"BUY", "SELL"}

    @property
    def is_event(self) -> bool:
        return self.signal_type == "EVENT"


def parse_signal_payload(payload: dict[str, Any]) -> SignalMessage:
    if not isinstance(payload, dict):
        raise SignalValidationError("Signal payload must be a JSON object")

    signal_type = _as_text(payload.get("type"), field_name="type").upper()
    if signal_type not in SUPPORTED_SIGNAL_TYPES:
        raise SignalValidationError(f"Unsupported signal type '{payload.get('type')}'")

    ticker = _as_text(payload.get("ticker"), field_name="ticker").upper()
    company_name = _as_text(payload.get("company_name"), field_name="company_name")

    market_value = payload.get("market")
    if market_value is None or (isinstance(market_value, str) and not market_value.strip()):
        market = infer_market(ticker)
    else:
        market = _as_text(market_value, field_name="market").upper()
    if market not in SUPPORTED_MARKETS:
        raise SignalValidationError(f"Unsupported market '{payload.get('market')}'")

    if signal_type in {"BUY", "SELL"} and not ticker:
        raise SignalValidationError("Trading signals require 'ticker'")

    price = _as_float(payload.get("price"), field_name="price")
    if signal_type in {"BUY", "SELL"} and price is None:
        raise SignalValidationError("Trading signals require 'price'")
    if signal_type in {"BUY", "SELL"} and price is not None and price <= 0:
        raise SignalValidationError("'price' must be greater than 0")
    if payload.get("buy_amount") not in (None, ""):
        _as_positive_float(payload.get("buy_amount"), field_name="buy_amount")

    position_fraction = _as_float(payload.get("position_fraction", 1.0), field_name="position_fraction")
    if position_fraction is None or not 0 < position_fraction <= 1:
        raise SignalValidationError("'position_fraction' must be greater than 0 and at most 1")
    sell_denominator = _as_int(payload.get("sell_denominator", 1), field_name="sell_denominator")
    if sell_denominator is None or sell_denominator < 1:
        raise SignalValidationError("'sell_denominator' must be a positive integer")
    sell_fraction = None
    if "sell_fraction" in payload:
        sell_fraction = _as_float(payload["sell_fraction"], field_name="sell_fraction")
        if sell_fraction is None or not 0 < sell_fraction <= 1:
            raise SignalValidationError("'sell_fraction' must be greater than 0 and at most 1")

    return SignalMessage(
        signal_type=signal_type,
        ticker=ticker,
        company_name=company_name or ticker,
        market=market,
        price=price,
        target_price=_as_positive_float(payload.get("target_price"), field_name="target_price"),
        stop_loss=_as_positive_float(payload.get("stop_loss"), field_name="stop_loss"),
        buy_score=_as_int(payload.get("buy_score"), field_name="buy_score"),
        rationale=_as_text(payload.get("rationale"), field_name="rationale"),
        profit_rate=_as_float(payload.get("profit_rate"), field_name="profit_rate"),
        sell_reason=_as_text(payload.get("sell_reason"), field_name="sell_reason"),
        buy_price=_as_positive_float(payload.get("buy_price"), field_name="buy_price"),
        position_fraction=position_fraction,
        sell_denominator=sell_denominator,
        sell_fraction=sell_fraction,
        event_type=_as_text(payload.get("event_type"), field_name="event_type"),
        event_source=_as_text(payload.get("source"), field_name="source"),
        event_description=_as_text(payload.get("event_description"), field_name="event_description"),
        raw=dict(payload),
    )


def parse_signal_bytes(message_bytes: bytes) -> SignalMessage:
    if not isinstance(message_bytes, (bytes, bytearray)):
        raise SignalValidationError("Signal payload must be UTF-8 JSON bytes")
    try:
        payload = load_signal_json(bytes(message_bytes).decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SignalValidationError("Signal payload must be UTF-8 JSON") from exc
    except json.JSONDecodeError as exc:
        raise SignalValidationError("Signal payload must be valid JSON") from exc

    return parse_signal_payload(payload)


TradingSignal = SignalMessage
parse_signal = parse_signal_payload
