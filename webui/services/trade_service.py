"""WebUI trading actions with explicit arming and safe defaults."""

from __future__ import annotations

import os
from dataclasses import asdict
from typing import Any
from pathlib import Path

from trading.schema import parse_signal_payload
from webui.services.account_service import build_manual_signal
from webui.services.masking import mask_text

ARM_PHRASE = "EXECUTE LIVE ORDER"
SELL_ALL_PHRASE = "SELL ALL POSITION"
TradeDispatcher = None


def live_trading_enabled(env: dict[str, str] | None = None) -> bool:
    source = env if env is not None else os.environ
    return str(source.get("WEBUI_ENABLE_LIVE_TRADING") or "").strip().lower() in {"1", "true", "yes", "on"}


def trading_guard_status(*, force_dry_run: bool = False) -> dict[str, Any]:
    enabled = live_trading_enabled()
    return {
        "enabled": enabled and not force_dry_run,
        "force_dry_run": force_dry_run,
        "arm_phrase": ARM_PHRASE,
        "sell_all_phrase": SELL_ALL_PHRASE,
        "message": (
            "This WebUI inherits subscriber dry-run mode; broker calls are disabled."
            if force_dry_run
            else
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
    force_dry_run: bool = False,
    queue_path: Path | None = None,
    work_tracker: Any | None = None,
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
    if not force_dry_run and not live_trading_enabled():
        return {
            "ok": False,
            "blocked": True,
            "signal": asdict(signal) | {"raw": None},
            "result": None,
            "error": "Live trading is disabled. Set WEBUI_ENABLE_LIVE_TRADING=true only on a trusted local machine.",
        }
    expected_phrase = SELL_ALL_PHRASE if signal.signal_type == "SELL" else ARM_PHRASE
    if not force_dry_run and arm_phrase.strip() != expected_phrase:
        return {
            "ok": False,
            "blocked": True,
            "signal": asdict(signal) | {"raw": None},
            "result": None,
            "error": f"Type {expected_phrase!r} to confirm this broker order.",
        }

    try:
        tracker_started = False
        if work_tracker is not None:
            tracker_started = work_tracker.begin()
            if not tracker_started:
                return {
                    "ok": False,
                    "blocked": True,
                    "signal": asdict(signal) | {"raw": None},
                    "result": None,
                    "error": "Subscriber shutdown is in progress; order was not sent.",
                }
        dispatcher_class = TradeDispatcher
        try:
            if dispatcher_class is None:
                from trading.dispatch import TradeDispatcher as dispatcher_class

            dispatcher = dispatcher_class(
                dry_run=force_dry_run,
                queue_path=queue_path,
                trading_mode=trading_mode or None,
                strategy_config={"name": ""},
                account_name=account_name or None,
            )
            result = await dispatcher.dispatch(signal)
        finally:
            if tracker_started:
                work_tracker.end()
        safe_result = asdict(result)
        safe_result["message"] = mask_text(safe_result.get("message"), os.environ)
        accepted_statuses = {"executed", "queued", "acknowledged", "dry-run"}
        return {
            "ok": result.status in accepted_statuses,
            "blocked": False,
            "signal": asdict(signal) | {"raw": None},
            "result": safe_result,
            "error": None if result.status in accepted_statuses else mask_text(result.message, os.environ),
        }
    except Exception as exc:  # noqa: BLE001 - safe UI boundary
        return {
            "ok": False,
            "blocked": False,
            "signal": asdict(signal) | {"raw": None},
            "result": None,
            "error": mask_text(exc, os.environ),
        }
