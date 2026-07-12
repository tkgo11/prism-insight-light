"""WebUI trading actions with explicit arming and safe defaults."""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any

from trading.dispatch import TradeDispatcher
from trading.schema import parse_signal_payload
from webui.services.account_service import build_manual_signal
from webui.services.masking import mask_text

ARM_PHRASE = "EXECUTE LIVE ORDER"


def live_trading_enabled(env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return str(source.get("WEBUI_ENABLE_LIVE_TRADING") or "").strip().lower() in {"1", "true", "yes", "on"}


def trading_guard_status() -> dict[str, Any]:
    enabled = live_trading_enabled()
    return {
        "enabled": enabled,
        "arm_phrase": ARM_PHRASE,
        "message": (
            "Live order controls are enabled; each order still requires the arming phrase."
            if enabled
            else "Live order controls are locked. Set WEBUI_ENABLE_LIVE_TRADING=true to allow broker calls."
        ),
    }


async def dispatch_manual_order(
    *,
    action: str,
    ticker: str,
    price: float,
    company_name: str = "",
    market: str = "auto",
    trading_mode: str | None = None,
    arm_phrase: str = "",
    account_name: str = "",
) -> dict[str, Any]:
    signal = None
    try:
        signal_payload = build_manual_signal(
            action=action,
            ticker=ticker,
            price=price,
            company_name=company_name,
            market=market,
        )
        signal = parse_signal_payload(signal_payload)
    except (TypeError, ValueError) as exc:
        raise ValueError(str(exc)) from exc
    if not live_trading_enabled():
        return {
            "ok": False,
            "blocked": True,
            "signal": asdict(signal) | {"raw": None},
            "result": None,
            "error": "Live trading is disabled. Set WEBUI_ENABLE_LIVE_TRADING=true only on a trusted local machine.",
        }
    if arm_phrase.strip() != ARM_PHRASE:
        return {
            "ok": False,
            "blocked": True,
            "signal": asdict(signal) | {"raw": None},
            "result": None,
            "error": f"Type {ARM_PHRASE!r} to confirm this broker order.",
        }

    try:
        dispatcher = TradeDispatcher(dry_run=False, trading_mode=trading_mode or None, strategy_config={"name": ""}, account_name=account_name or None)
        result = await dispatcher.dispatch(signal)
        return {
            "ok": result.status in {"executed", "queued", "acknowledged"},
            "blocked": False,
            "signal": asdict(signal) | {"raw": None},
            "result": asdict(result),
            "error": None if result.status in {"executed", "queued", "acknowledged"} else mask_text(result.message, os.environ),
        }
    except Exception as exc:  # noqa: BLE001 - safe UI boundary
        return {
            "ok": False,
            "blocked": False,
            "signal": asdict(signal) | {"raw": None},
            "result": None,
            "error": mask_text(exc, os.environ),
        }
