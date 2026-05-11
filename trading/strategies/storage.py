"""Persistent strategy basket and cooldown state."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class ClaimedBasket:
    group_id: str
    flush_id: str
    strategy_name: str
    market: str
    account_name: str
    signals: dict[str, dict[str, Any]]
    claimed_at: str


@dataclass(slots=True)
class StrategyBasketRecord:
    group_id: str
    strategy_name: str
    market: str
    account_name: str
    signals: dict[str, dict[str, Any]] = field(default_factory=dict)
    claimed_by_flush_id: str | None = None
    claimed_at: str | None = None
    created_at: str = field(default_factory=lambda: _utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: _utcnow().isoformat())


class StrategyBasketStore:
    def __init__(self, storage_path: Path | None = None):
        self.storage_path = storage_path or Path("runtime") / "strategy_baskets.json"
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> list[StrategyBasketRecord]:
        if not self.storage_path.exists():
            return []
        raw = json.loads(self.storage_path.read_text(encoding="utf-8"))
        return [StrategyBasketRecord(**item) for item in raw]

    def _save(self, items: list[StrategyBasketRecord]) -> None:
        payload = [asdict(item) for item in items]
        self.storage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def collect(self, *, strategy_name: str, market: str, account_name: str, signal_payload: dict[str, Any]) -> str:
        records = self._load()
        ticker = str(signal_payload.get("ticker", "")).strip().upper()
        if not ticker:
            raise ValueError("Strategy basket signals require a ticker")

        for record in records:
            if (
                record.strategy_name == strategy_name
                and record.market == market
                and record.account_name == account_name
            ):
                record.signals[ticker] = dict(signal_payload)
                record.updated_at = _utcnow().isoformat()
                self._save(records)
                return record.group_id

        record = StrategyBasketRecord(
            group_id=uuid4().hex,
            strategy_name=strategy_name,
            market=market,
            account_name=account_name,
            signals={ticker: dict(signal_payload)},
        )
        records.append(record)
        self._save(records)
        return record.group_id

    def claim_group(self, group_id: str) -> ClaimedBasket | None:
        records = self._load()
        for record in records:
            if record.group_id != group_id:
                continue
            if record.claimed_by_flush_id or not record.signals:
                return None
            flush_id = uuid4().hex
            claimed_at = _utcnow().isoformat()
            record.claimed_by_flush_id = flush_id
            record.claimed_at = claimed_at
            record.updated_at = claimed_at
            self._save(records)
            return ClaimedBasket(
                group_id=record.group_id,
                flush_id=flush_id,
                strategy_name=record.strategy_name,
                market=record.market,
                account_name=record.account_name,
                signals=dict(record.signals),
                claimed_at=claimed_at,
            )
        return None

    def claim_all(self) -> list[ClaimedBasket]:
        records = self._load()
        claimed: list[ClaimedBasket] = []
        changed = False
        for record in records:
            if record.claimed_by_flush_id or not record.signals:
                continue
            flush_id = uuid4().hex
            claimed_at = _utcnow().isoformat()
            record.claimed_by_flush_id = flush_id
            record.claimed_at = claimed_at
            record.updated_at = claimed_at
            changed = True
            claimed.append(
                ClaimedBasket(
                    group_id=record.group_id,
                    flush_id=flush_id,
                    strategy_name=record.strategy_name,
                    market=record.market,
                    account_name=record.account_name,
                    signals=dict(record.signals),
                    claimed_at=claimed_at,
                )
            )
        if changed:
            self._save(records)
        return claimed

    def complete_flush(self, *, group_id: str, flush_id: str, remaining_signals: dict[str, dict[str, Any]]) -> None:
        records = self._load()
        updated_records: list[StrategyBasketRecord] = []
        for record in records:
            if record.group_id != group_id:
                updated_records.append(record)
                continue
            if record.claimed_by_flush_id != flush_id:
                updated_records.append(record)
                continue
            if remaining_signals:
                record.signals = dict(remaining_signals)
                record.claimed_by_flush_id = None
                record.claimed_at = None
                record.updated_at = _utcnow().isoformat()
                updated_records.append(record)
        self._save(updated_records)

    def pending_count(self) -> int:
        return sum(len(record.signals) for record in self._load())


class StrategyStateStore:
    def __init__(self, storage_path: Path | None = None):
        self.storage_path = storage_path or Path("runtime") / "strategy_state.json"
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not self.storage_path.exists():
            return {"cooldowns": {}, "owned_positions": {}}
        return json.loads(self.storage_path.read_text(encoding="utf-8"))

    def _save(self, payload: dict[str, Any]) -> None:
        self.storage_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _cooldown_key(market: str, account_id: str, ticker: str) -> str:
        return f"{market}:{account_id}:{ticker.upper()}"

    @staticmethod
    def _ownership_key(market: str, account_id: str) -> str:
        return f"{market}:{account_id}"

    def in_cooldown(
        self,
        *,
        market: str,
        account_id: str,
        ticker: str,
        cooldown_window: timedelta,
        now: datetime | None = None,
    ) -> bool:
        payload = self._load()
        key = self._cooldown_key(market, account_id, ticker)
        value = payload.get("cooldowns", {}).get(key)
        if not value:
            return False
        cutoff = (now or _utcnow()) - cooldown_window
        return datetime.fromisoformat(value) > cutoff

    def record_confirmed_buy(self, *, market: str, account_id: str, ticker: str, timestamp: str) -> None:
        payload = self._load()
        payload.setdefault("cooldowns", {})[self._cooldown_key(market, account_id, ticker)] = timestamp
        self._save(payload)

    def get_owned_positions(self, *, market: str, account_id: str) -> set[str]:
        payload = self._load()
        values = payload.get("owned_positions", {}).get(self._ownership_key(market, account_id), [])
        return {str(item).upper() for item in values}

    def set_owned_positions(self, *, market: str, account_id: str, tickers: set[str]) -> None:
        payload = self._load()
        payload.setdefault("owned_positions", {})[self._ownership_key(market, account_id)] = sorted(
            ticker.upper() for ticker in tickers
        )
        self._save(payload)
