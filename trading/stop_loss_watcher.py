"""Real-time stop-loss monitoring and automated sell execution."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .domestic import DomesticStockTrading, MultiAccountDomesticStockTrading
from .file_lock import FileLock
from .market_hours import is_market_open
from .schema import SignalMessage, parse_signal_payload
from .us import MultiAccountUSStockTrading, USStockTrading

logger = logging.getLogger("trading.stop_loss_watcher")

DEFAULT_STOP_LOSS_POSITIONS_PATH = Path("runtime") / "stop_loss_positions.json"


def _as_enabled(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True, slots=True)
class StopLossWatcherConfig:
    """Configuration for real-time stop-loss price monitoring."""

    enabled: bool = False
    poll_seconds: float = 5.0
    request_interval_seconds: float = 0.2
    storage_path: Path = field(default_factory=lambda: DEFAULT_STOP_LOSS_POSITIONS_PATH)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "StopLossWatcherConfig":
        if not payload or not isinstance(payload, dict):
            return cls()
        enabled = _as_enabled(payload.get("enabled"), default=False)
        try:
            poll_seconds = float(payload.get("poll_seconds", 5.0))
            if poll_seconds <= 0:
                poll_seconds = 5.0
        except (TypeError, ValueError):
            poll_seconds = 5.0

        try:
            request_interval = float(payload.get("request_interval_seconds", 0.2))
            if request_interval < 0:
                request_interval = 0.2
        except (TypeError, ValueError):
            request_interval = 0.2

        storage_raw = payload.get("storage_path")
        storage_path = Path(storage_raw) if storage_raw else DEFAULT_STOP_LOSS_POSITIONS_PATH

        return cls(
            enabled=enabled,
            poll_seconds=poll_seconds,
            request_interval_seconds=request_interval,
            storage_path=storage_path,
        )


@dataclass(slots=True)
class TrackedPosition:
    market: str
    ticker: str
    stop_loss: float
    entry_price: float = 0.0
    company_name: str = ""
    target_price: float | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrackedPosition":
        return cls(
            market=str(data.get("market", "")).upper(),
            ticker=str(data.get("ticker", "")),
            stop_loss=float(data.get("stop_loss", 0.0)),
            entry_price=float(data.get("entry_price", 0.0)),
            company_name=str(data.get("company_name", "")),
            target_price=(
                float(data["target_price"])
                if data.get("target_price") is not None
                else None
            ),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )


class StopLossTracker:
    """Thread-safe and process-safe JSON ledger for active stop-loss positions."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_STOP_LOSS_POSITIONS_PATH
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            os.chmod(self.path.parent, 0o700)

    def _key(self, market: str, ticker: str) -> str:
        return f"{market.strip().upper()}:{ticker.strip()}"

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
            if isinstance(data, list):
                result = {}
                for item in data:
                    if isinstance(item, dict) and "market" in item and "ticker" in item:
                        result[self._key(item["market"], item["ticker"])] = item
                return result
            return {}
        except FileNotFoundError:
            return {}
        except Exception as exc:
            logger.warning("Could not read stop-loss positions file (%s): %s", self.path, exc)
            return {}

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        temp_file = self.path.with_suffix(".tmp")
        temp_file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        if os.name != "nt":
            os.chmod(temp_file, 0o600)
        temp_file.replace(self.path)

    def record_position(
        self,
        market: str,
        ticker: str,
        stop_loss: float,
        *,
        entry_price: float = 0.0,
        company_name: str = "",
        target_price: float | None = None,
    ) -> None:
        """Register or update a position's stop-loss price."""
        if stop_loss <= 0:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        key = self._key(market, ticker)
        with FileLock(self.lock_path):
            positions = self._load()
            existing = positions.get(key)
            created_at = existing.get("created_at") if existing else now_iso
            pos = TrackedPosition(
                market=market.upper(),
                ticker=ticker,
                stop_loss=float(stop_loss),
                entry_price=float(entry_price),
                company_name=company_name or ticker,
                target_price=float(target_price) if target_price is not None else None,
                created_at=created_at or now_iso,
                updated_at=now_iso,
            )
            positions[key] = pos.to_dict()
            self._save(positions)
            logger.info(
                "[StopLossTracker] Registered %s %s with stop_loss=%s (entry=%s)",
                market.upper(),
                ticker,
                stop_loss,
                entry_price,
            )

    def remove_position(self, market: str, ticker: str) -> bool:
        """Remove a position from stop-loss tracking."""
        key = self._key(market, ticker)
        with FileLock(self.lock_path):
            positions = self._load()
            if key in positions:
                del positions[key]
                self._save(positions)
                logger.info("[StopLossTracker] Removed %s %s from stop-loss tracking", market.upper(), ticker)
                return True
            return False

    def get_positions(self, market: str | None = None) -> list[dict[str, Any]]:
        """Return all tracked positions, optionally filtered by market."""
        with FileLock(self.lock_path):
            data = self._load()
            items = list(data.values())
        if market:
            target_market = market.strip().upper()
            return [item for item in items if item.get("market", "").upper() == target_market]
        return items

    def get_position(self, market: str, ticker: str) -> dict[str, Any] | None:
        key = self._key(market, ticker)
        with FileLock(self.lock_path):
            data = self._load()
            return data.get(key)

    def clear(self) -> None:
        with FileLock(self.lock_path):
            self._save({})


class StopLossWatcher:
    """Background service that periodically checks positions against stop-loss thresholds."""

    def __init__(
        self,
        dispatcher: Any,
        config: StopLossWatcherConfig,
        tracker: StopLossTracker | None = None,
        work_tracker: Any = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.config = config
        self.tracker = tracker or StopLossTracker(config.storage_path)
        self.work_tracker = work_tracker
        self._stop_event = threading.Event()
        self._activity_lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.config.enabled:
            logger.info("Stop-loss watcher is disabled by configuration")
            return
        if getattr(self.dispatcher, "dry_run", False):
            logger.info("Stop-loss watcher disabled in dry-run mode")
            return
        self._thread = threading.Thread(target=self._run, name="stop-loss-watcher", daemon=True)
        self._thread.start()
        logger.info(
            "Stop-loss watcher started (poll_seconds=%s, request_interval=%ss)",
            self.config.poll_seconds,
            self.config.request_interval_seconds,
        )

    def request_stop(self) -> None:
        with self._activity_lock:
            self._stop_event.set()

    def stop(self) -> None:
        self.request_stop()
        if self._thread:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                logger.warning("Stop-loss watcher thread did not exit cleanly")
            else:
                logger.info("Stop-loss watcher stopped")

    def _run(self) -> None:
        while not self._stop_event.wait(self.config.poll_seconds):
            with self._activity_lock:
                if self._stop_event.is_set():
                    return
                if self.work_tracker is not None and not self.work_tracker.begin():
                    return
            try:
                self.check_stop_loss_once()
            except Exception as exc:  # noqa: BLE001
                logger.exception("Stop-loss watcher error during check cycle: %s", exc)
            finally:
                if self.work_tracker is not None:
                    self.work_tracker.end()

    def _get_trader(self, market: str):
        mode = getattr(self.dispatcher, "trading_mode", "real")
        multi_account = getattr(self.dispatcher, "multi_account_enabled", False)
        if market == "US":
            return MultiAccountUSStockTrading(mode=mode) if multi_account else USStockTrading(mode=mode)
        return MultiAccountDomesticStockTrading(mode=mode) if multi_account else DomesticStockTrading(mode=mode)

    def check_stop_loss_once(self) -> list[dict[str, Any]]:
        """Inspect tracked positions and trigger automatic SELL when current price <= stop_loss."""
        triggered_results: list[dict[str, Any]] = []

        for market in ("KR", "US"):
            if self._stop_event.is_set():
                break

            if not is_market_open(market):
                continue

            positions = self.tracker.get_positions(market=market)
            if not positions:
                continue

            trader = self._get_trader(market)
            for pos in positions:
                if self._stop_event.is_set():
                    break

                ticker = pos.get("ticker", "")
                stop_loss = float(pos.get("stop_loss", 0.0))
                if not ticker or stop_loss <= 0:
                    continue

                if self.config.request_interval_seconds > 0:
                    time.sleep(self.config.request_interval_seconds)

                try:
                    price_info = trader.get_current_price(ticker)
                except Exception as exc:
                    logger.warning("[Stop-Loss] Error fetching current price for %s %s: %s", market, ticker, exc)
                    continue

                if not price_info or not isinstance(price_info, dict):
                    continue

                current_price = float(price_info.get("current_price", 0.0))
                if current_price <= 0:
                    continue

                try:
                    holding_qty = trader.get_holding_quantity(ticker)
                except Exception as exc:
                    logger.warning("[Stop-Loss] Could not check holding quantity for %s %s: %s", market, ticker, exc)
                    holding_qty = 1

                if holding_qty <= 0:
                    logger.info(
                        "[Stop-Loss] %s %s has 0 holding quantity; clearing from tracking",
                        market,
                        ticker,
                    )
                    self.tracker.remove_position(market, ticker)
                    continue

                if current_price <= stop_loss:
                    logger.warning(
                        "[Stop-Loss Triggered] %s %s current price (%s) reached stop_loss (%s). Executing automatic SELL.",
                        market,
                        ticker,
                        current_price,
                        stop_loss,
                    )
                    action_result = self._trigger_sell(market, ticker, current_price, pos)
                    triggered_results.append(action_result)

        return triggered_results

    def _trigger_sell(
        self, market: str, ticker: str, current_price: float, pos: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch an automated stop-loss SELL signal."""
        signal_id = f"stop_loss_{market}_{ticker}_{int(time.time())}"
        signal_payload = {
            "signal_id": signal_id,
            "type": "SELL",
            "signal_type": "SELL",
            "market": market,
            "ticker": ticker,
            "company_name": pos.get("company_name", ticker),
            "price": current_price,
            "stop_loss": pos.get("stop_loss"),
            "sell_reason": "stop_loss",
        }
        signal = parse_signal_payload(signal_payload)

        try:
            result = asyncio.run(self.dispatcher.dispatch(signal, allow_queue=False))
            logger.info(
                "[Stop-Loss Sell Result] %s %s -> status=%s message=%s",
                market,
                ticker,
                result.status,
                result.message,
            )
            if result.status in {"executed", "dry-run"}:
                self.tracker.remove_position(market, ticker)
            return {
                "market": market,
                "ticker": ticker,
                "current_price": current_price,
                "stop_loss": pos.get("stop_loss"),
                "status": result.status,
                "message": result.message,
            }
        except Exception as exc:
            logger.exception("[Stop-Loss Sell Failed] %s %s: %s", market, ticker, exc)
            return {
                "market": market,
                "ticker": ticker,
                "current_price": current_price,
                "stop_loss": pos.get("stop_loss"),
                "status": "failed",
                "error": str(exc),
            }