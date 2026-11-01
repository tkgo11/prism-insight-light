"""Real-time stop-loss monitoring and automated sell execution."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config_paths import runtime_file_path
from .domestic import DomesticStockTrading, MultiAccountDomesticStockTrading
from .file_lock import FileLock
from .market_hours import is_market_open
from .schema import SignalMessage, parse_signal_payload
from .us import MultiAccountUSStockTrading, USStockTrading

logger = logging.getLogger("trading.stop_loss_watcher")

DEFAULT_STOP_LOSS_POSITIONS_PATH = Path("runtime") / "stop_loss_positions.json"


def default_stop_loss_positions_path() -> Path:
    return runtime_file_path(DEFAULT_STOP_LOSS_POSITIONS_PATH)


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
    holding_check_interval_seconds: float = 60.0
    storage_path: Path = field(default_factory=default_stop_loss_positions_path)

    def __post_init__(self) -> None:
        # Clamp non-finite timing values: Event.wait(inf)/sleep(nan) raise
        # OverflowError/ValueError, which would silently kill the watcher
        # thread. Covers every construction path (from_mapping, CLI flags).
        if not math.isfinite(self.poll_seconds) or self.poll_seconds <= 0:
            object.__setattr__(self, "poll_seconds", 5.0)
        if not math.isfinite(self.request_interval_seconds) or self.request_interval_seconds < 0:
            object.__setattr__(self, "request_interval_seconds", 0.2)
        if (
            not math.isfinite(self.holding_check_interval_seconds)
            or self.holding_check_interval_seconds < 0
        ):
            object.__setattr__(self, "holding_check_interval_seconds", 60.0)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any] | None) -> "StopLossWatcherConfig":
        if not payload or not isinstance(payload, dict):
            return cls()
        enabled = _as_enabled(payload.get("enabled"), default=False)
        try:
            poll_seconds = float(payload.get("poll_seconds", 5.0))
            if poll_seconds <= 0:
                poll_seconds = 5.0
        except (TypeError, ValueError, OverflowError):
            poll_seconds = 5.0

        try:
            request_interval = float(payload.get("request_interval_seconds", 0.2))
            if request_interval < 0:
                request_interval = 0.2
        except (TypeError, ValueError, OverflowError):
            request_interval = 0.2

        try:
            holding_check_interval = float(payload.get("holding_check_interval_seconds", 60.0))
            if holding_check_interval < 0:
                holding_check_interval = 60.0
        except (TypeError, ValueError, OverflowError):
            holding_check_interval = 60.0

        storage_raw = payload.get("storage_path")
        storage_path = Path(storage_raw) if storage_raw else default_stop_loss_positions_path()

        return cls(
            enabled=enabled,
            poll_seconds=poll_seconds,
            request_interval_seconds=request_interval,
            holding_check_interval_seconds=holding_check_interval,
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
        self.path = path or default_stop_loss_positions_path()
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            if not self.path.parent.is_dir():
                raise
        else:
            if os.name != "nt":
                os.chmod(self.path.parent, 0o700)

    def _key(self, market: str, ticker: str) -> str:
        return f"{market.strip().upper()}:{ticker.strip()}"

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = self.path.read_text(encoding="utf-8")
            # Duplicate JSON keys silently overwrite an earlier stop price with
            # Python's default decoder. Treat the entire ledger as ambiguous
            # rather than trading against an arbitrary surviving record.
            def unique_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
                obj: dict[str, Any] = {}
                for key, value in pairs:
                    if key in obj:
                        raise ValueError("Duplicate key in stop-loss ledger JSON")
                    obj[key] = value
                return obj

            data = json.loads(raw, object_pairs_hook=unique_object_pairs)
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            logger.error(
                "Stop-loss positions file is unreadable (%s): %s", self.path, exc
            )
            raise RuntimeError(
                "Stop-loss tracker is unreadable; automated stop-loss actions are blocked"
            ) from exc

        if isinstance(data, dict):
            return data
        if isinstance(data, list):
            result = {}
            first_records: dict[str, tuple[str, int]] = {}
            ambiguous_keys: set[str] = set()
            for index, item in enumerate(data):
                # Legacy lists may contain malformed or duplicate entries.  Keep
                # their original payload under a non-tradable key when the list
                # is migrated to a dictionary by a later write.  Dropping them
                # would destroy the operator's evidence for reconciliation.
                quarantine_key = f"__legacy_unverified_{index}__"
                if not isinstance(item, dict):
                    logger.error("Quarantining malformed legacy stop-loss record at index %s", index)
                    result[quarantine_key] = item
                    continue
                market, ticker = item.get("market"), item.get("ticker")
                if (
                    not isinstance(market, str)
                    or not isinstance(ticker, str)
                    or not market.strip()
                    or not ticker.strip()
                ):
                    # A single corrupt legacy record must not prevent checks
                    # for every other valid tracked position.
                    logger.error("Quarantining legacy stop-loss record with invalid market/ticker at index %s", index)
                    result[quarantine_key] = item
                    continue
                key = self._key(market, ticker)
                # Case variants of a US ticker refer to the same instrument.
                canonical_key = f"{market.strip().upper()}:{ticker.strip().upper()}"
                if canonical_key in ambiguous_keys:
                    logger.error("Quarantining additional duplicate legacy stop-loss record at index %s", index)
                    result[quarantine_key] = item
                    continue
                if canonical_key in first_records:
                    # Quarantine both conflicting records, including case variants.
                    first_key, first_index = first_records.pop(canonical_key)
                    result[f"__legacy_unverified_{first_index}__"] = result.pop(first_key)
                    ambiguous_keys.add(canonical_key)
                    logger.error("Quarantining conflicting legacy stop-loss records for %s", canonical_key)
                    result[quarantine_key] = item
                    continue
                result[key] = item
                first_records[canonical_key] = (key, index)
            return result
        raise RuntimeError(
            "Stop-loss tracker has an invalid format; automated stop-loss actions are blocked"
        )

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        # A fixed sibling such as path.with_suffix(".tmp") can be the actual
        # configured ledger path (or collide with another writer). Use a unique
        # same-directory file so replacement stays atomic even for *.tmp paths.
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                if os.name != "nt":
                    os.chmod(temporary_path, 0o600)
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

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
        # Reject malformed identifiers before touching durable protection.
        # Whitespace-only instruments otherwise create unreachable ledger
        # entries that the watcher silently skips; normalize outer whitespace
        # so the persisted key and the broker-facing ticker agree.
        if not isinstance(market, str) or market.strip().upper() not in {"KR", "US"}:
            raise ValueError("Stop-loss position market must be KR or US")
        if not isinstance(ticker, str) or not ticker.strip():
            raise ValueError("Stop-loss position ticker must be nonempty")
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in market + ticker):
            raise ValueError("Stop-loss position identifiers contain control characters")
        market = market.strip().upper()
        # US symbols are case-insensitive. New writes must use one canonical
        # key, or a lowercase re-registration can create two active records
        # and cause the watcher to quarantine both, losing protection.
        ticker = ticker.strip().upper() if market == "US" else ticker.strip()
        # Invalid/non-finite prices must not overwrite a valid protection
        # record with JSON NaN/Infinity that the watcher later skips.
        try:
            if any(isinstance(value, bool) for value in (stop_loss, entry_price, target_price)):
                raise ValueError("Boolean price is not valid")
            stop_loss_value = float(stop_loss)
            entry_price_value = float(entry_price)
            target_price_value = float(target_price) if target_price is not None else None
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("Stop-loss position contains invalid prices") from exc
        prices = (stop_loss_value, entry_price_value)
        if target_price_value is not None:
            prices += (target_price_value,)
        if not all(math.isfinite(value) for value in prices):
            raise ValueError("Stop-loss position prices must be finite")
        if entry_price_value < 0 or (target_price_value is not None and target_price_value < 0):
            raise ValueError("Stop-loss position prices must be nonnegative")
        if stop_loss_value <= 0:
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        key = self._key(market, ticker)
        with FileLock(self.lock_path):
            positions = self._load()
            # Never silently overwrite or duplicate a legacy case-variant or
            # quarantined instrument. Its original evidence must be reconciled
            # by an operator before a new stop level can become actionable.
            for stored_key, stored in positions.items():
                if not isinstance(stored, dict):
                    continue
                stored_market, stored_ticker = stored.get("market"), stored.get("ticker")
                if not isinstance(stored_market, str) or not isinstance(stored_ticker, str):
                    continue
                if (stored_market.strip().upper() == market
                        and stored_ticker.strip().upper() == ticker.upper()
                        and stored_key != key):
                    raise ValueError(
                        "Conflicting stop-loss instrument requires operator reconciliation"
                    )
            existing = positions.get(key)
            created_at = existing.get("created_at") if isinstance(existing, dict) else now_iso
            pos = TrackedPosition(
                market=market.upper(),
                ticker=ticker,
                stop_loss=stop_loss_value,
                entry_price=entry_price_value,
                company_name=company_name or ticker,
                target_price=target_price_value,
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

    def remove_position(
        self,
        market: str,
        ticker: str,
        *,
        expected_updated_at: Any = None,
        expected_record: dict[str, Any] | None = None,
    ) -> bool:
        """Remove a position from stop-loss tracking.

        When ``expected_updated_at`` is given, the record is deleted only if it
        is still the same tracked record the caller inspected — a position that
        was re-registered by a concurrent BUY must not be silently wiped.
        ``updated_at`` (not ``created_at``) is the discriminator because
        re-registration preserves the original ``created_at``. Pass
        ``expected_record`` for an exact compare-and-swap check, including
        legacy records without ``updated_at``; otherwise a concurrent BUY
        could lose its newly registered stop-loss protection.
        """
        key = self._key(market, ticker)
        with FileLock(self.lock_path):
            positions = self._load()
            record = positions.get(key)
            if record is None:
                return False
            if not isinstance(record, dict):
                logger.error("[StopLossTracker] Refusing to delete malformed record for %s %s", market, ticker)
                return False
            if expected_record is not None and record != expected_record:
                logger.info("[StopLossTracker] Keeping %s %s: record changed since inspection", market, ticker)
                return False
            if expected_updated_at is not None and record.get("updated_at") != expected_updated_at:
                logger.info(
                    "[StopLossTracker] Keeping %s %s: tracked record changed since inspection",
                    market.upper(),
                    ticker,
                )
                return False
            del positions[key]
            self._save(positions)
            logger.info("[StopLossTracker] Removed %s %s from stop-loss tracking", market.upper(), ticker)
            return True

    def get_positions(self, market: str | None = None) -> list[dict[str, Any]]:
        """Return valid tracked positions without discarding corrupt records on disk."""
        def valid_stored_price(value: Any, *, positive: bool = False) -> bool:
            # Legacy/manual JSON can contain booleans or NaN/Infinity. A bool
            # becomes 1.0 under float(), and nonfinite stop levels could
            # silently disable protection or trigger an unintended order.
            if isinstance(value, bool):
                return False
            try:
                number = float(value)
            except (TypeError, ValueError, OverflowError):
                return False
            return math.isfinite(number) and (number > 0 if positive else number >= 0)

        with FileLock(self.lock_path):
            data = self._load()
            items = list(data.items())
        target_market = market.strip().upper() if market else None
        # Older versions may already have migrated a duplicate list into a
        # dictionary while leaving the first conflicting entry actionable.
        # Block that entry too until the operator reconciles the quarantine.
        quarantined_instruments: set[str] = set()
        active_instrument_counts: dict[str, int] = {}
        for key, item in items:
            if not isinstance(item, dict):
                continue
            q_market, q_ticker = item.get("market"), item.get("ticker")
            if not isinstance(q_market, str) or not isinstance(q_ticker, str) or not q_market.strip() or not q_ticker.strip():
                continue
            canonical_key = f"{q_market.strip().upper()}:{q_ticker.strip().upper()}"
            if key.startswith("__legacy_unverified_"):
                quarantined_instruments.add(canonical_key)
            elif self._key(q_market, q_ticker) == key:
                active_instrument_counts[canonical_key] = active_instrument_counts.get(canonical_key, 0) + 1
        valid: list[dict[str, Any]] = []
        for key, item in items:
            if isinstance(item, dict) and isinstance(item.get("market"), str) and isinstance(item.get("ticker"), str):
                canonical_key = f"{item['market'].strip().upper()}:{item['ticker'].strip().upper()}"
                if canonical_key in quarantined_instruments or active_instrument_counts.get(canonical_key, 0) > 1:
                    logger.error("Blocking ambiguous stop-loss instrument %s pending reconciliation", canonical_key)
                    continue
            if (
                not isinstance(item, dict)
                or not isinstance(item.get("market"), str)
                or not isinstance(item.get("ticker"), str)
                or not item["market"].strip()
                or not item["ticker"].strip()
                or item["market"].strip().upper() not in {"KR", "US"}
                or item["ticker"] != item["ticker"].strip()
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in item["market"] + item["ticker"])
                or self._key(item["market"], item["ticker"]) != key
                or not valid_stored_price(item.get("stop_loss"), positive=True)
                or not valid_stored_price(item.get("entry_price", 0))
                or (
                    item.get("target_price") is not None
                    and not valid_stored_price(item["target_price"])
                )
            ):
                # Never dispatch an order to a malformed or mismatched ticker.
                # Preserve the raw record on disk for operator reconciliation.
                logger.error("Skipping malformed stop-loss record at key %s", key)
                continue
            if target_market is None or item["market"].strip().upper() == target_market:
                valid.append(item)
        return valid

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
        self._traders: dict[str, Any] = {}
        self._last_holding_check: dict[str, float] = {}
        self._last_trigger_attempt: dict[str, float] = {}

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

    def _get_cached_trader(self, market: str):
        trader = self._traders.get(market)
        if trader is None:
            trader = self._get_trader(market)
            self._traders[market] = trader
        return trader

    def _holding_check_due(self, market: str, now: float) -> bool:
        last = self._last_holding_check.get(market)
        if last is None:
            return True
        return now - last >= self.config.holding_check_interval_seconds

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

            trader = self._get_cached_trader(market)
            cycle_started = time.monotonic()
            reconcile_holdings = self._holding_check_due(market, cycle_started)
            if reconcile_holdings:
                self._last_holding_check[market] = cycle_started
            for pos in positions:
                if self._stop_event.is_set():
                    break

                try:
                    ticker = str(pos.get("ticker", "") or "")
                    stop_loss = float(pos.get("stop_loss", 0.0))
                except (AttributeError, TypeError, ValueError, OverflowError):
                    # One malformed persisted record must not abort the cycle
                    # and leave every later position unchecked.
                    logger.error("[Stop-Loss] Skipping malformed tracked record: %r", pos)
                    continue
                if not ticker or not math.isfinite(stop_loss) or stop_loss <= 0:
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

                # A malformed quote must not abort this market's entire
                # watcher cycle and leave subsequent positions unprotected.
                try:
                    current_price = float(price_info.get("current_price", 0.0))
                except (TypeError, ValueError, OverflowError):
                    logger.warning(
                        "[Stop-Loss] Invalid current price for %s %s: %r",
                        market, ticker, price_info.get("current_price"),
                    )
                    continue
                if not math.isfinite(current_price) or current_price <= 0:
                    logger.warning(
                        "[Stop-Loss] Nonpositive or nonfinite current price for %s %s",
                        market, ticker,
                    )
                    continue

                stop_hit = current_price <= stop_loss
                if not stop_hit and not reconcile_holdings:
                    continue

                try:
                    holding_qty = self._validated_holding_quantity(
                        trader.get_holding_quantity(ticker)
                    )
                except Exception as exc:
                    logger.error(
                        "[Stop-Loss] Holding quantity is UNKNOWN for %s %s; "
                        "blocking automatic SELL: %s",
                        market,
                        ticker,
                        exc,
                    )
                    continue
                if holding_qty is None:
                    logger.error(
                        "[Stop-Loss] Invalid holding quantity for %s %s; "
                        "blocking automatic SELL and tracker removal",
                        market, ticker,
                    )
                    continue

                if holding_qty == 0:
                    logger.info(
                        "[Stop-Loss] %s %s has 0 holding quantity; clearing from tracking",
                        market,
                        ticker,
                    )
                    self.tracker.remove_position(
                        market, ticker, expected_updated_at=pos.get("updated_at"),
                        expected_record=pos,
                    )
                    continue

                if stop_hit:
                    # Price/holdings inquiries can take seconds. A concurrent
                    # BUY or operator stop-level edit may replace the snapshot
                    # while those broker calls are running. Do not liquidate
                    # against a stale threshold or an invalidated record.
                    latest = self.tracker.get_positions(market=market)
                    # Compare the full persisted snapshot, not only the
                    # threshold and updated_at. Legacy records can lack
                    # updated_at; a concurrent edit must invalidate the
                    # in-flight quote/holdings decision before broker submit.
                    if not any(current == pos for current in latest):
                        logger.warning(
                            "[Stop-Loss] Skipping stale or quarantined protection for %s %s",
                            market, ticker,
                        )
                        continue
                    key = self.tracker._key(market, ticker)
                    now_mono = time.monotonic()
                    last_attempt = self._last_trigger_attempt.get(key, 0.0)
                    if now_mono - last_attempt < 15.0:
                        logger.debug(
                            "[Stop-Loss] %s %s sell in backoff cooldown (%.1fs < 15.0s); skipping",
                            market, ticker, now_mono - last_attempt,
                        )
                        continue
                    self._last_trigger_attempt[key] = now_mono
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

    @staticmethod
    def _is_no_position_result(account_result: Any) -> bool:
        msg = str(getattr(account_result, "message", "") or "").lower()
        return any(
            phrase in msg
            for phrase in (
                "not found in portfolio",
                "not in portfolio",
                "holding quantity is 0",
                "holding quantity 0",
                "no holding quantity",
            )
        )

    @staticmethod
    def _validated_holding_quantity(value: Any) -> float | None:
        """Return a confirmed nonnegative finite balance, or fail closed."""
        if isinstance(value, bool):
            return None
        try:
            quantity = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(quantity) or quantity < 0:
            return None
        return quantity

    def _trigger_sell(
        self, market: str, ticker: str, current_price: float, pos: dict[str, Any]
    ) -> dict[str, Any]:
        """Dispatch an automated stop-loss SELL signal."""
        identity_material = "|".join(
            (
                market,
                ticker,
                str(pos.get("created_at") or ""),
                str(pos.get("entry_price") or ""),
                str(pos.get("stop_loss") or ""),
            )
        )
        signal_digest = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:20]
        signal_id = f"stop_loss_{market}_{ticker}_{signal_digest}"
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
            trader = self._get_cached_trader(market)
            # In multi-account setups, if the sell executed on holding accounts
            # and other accounts were already satisfied or had no position,
            # or if 0 shares remain across accounts, protection is safely dropped.
            all_accounts_clean = (
                bool(result.accounts)
                and any(
                    a.status == "executed"
                    or (a.status == "skipped" and getattr(a, "previous_status", None) == "executed")
                    for a in result.accounts
                )
                and all(
                    a.status == "executed"
                    or (a.status == "skipped" and getattr(a, "previous_status", None) == "executed")
                    or self._is_no_position_result(a)
                    for a in result.accounts
                )
            )
            if result.status in {"executed", "partial_success"} or all_accounts_clean:
                # Broker acceptance, duplicate suppression, and an account's
                # "not held" message do not establish that *all* accounts are
                # flat. Preserve the durable watcher until a fresh aggregate
                # holdings inquiry confirms exactly zero shares.
                try:
                    remaining_qty = self._validated_holding_quantity(
                        trader.get_holding_quantity(ticker)
                    )
                except Exception as exc:
                    logger.warning(
                        "[Stop-Loss] Keeping protection for %s %s: "
                        "post-order holdings cannot be verified: %s",
                        market, ticker, exc,
                    )
                else:
                    if remaining_qty == 0:
                        self.tracker.remove_position(
                            market, ticker, expected_updated_at=pos.get("updated_at"),
                            expected_record=pos,
                        )
                        self._last_trigger_attempt.pop(
                            self.tracker._key(market, ticker), None
                        )
                    else:
                        logger.warning(
                            "[Stop-Loss] Keeping protection for %s %s after "
                            "SELL (remaining=%s; order acceptance is not a fill)",
                            market, ticker, remaining_qty,
                        )
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