"""Market-normalized gateway layer for trading strategies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..domestic import AsyncTradingContext
from ..us import AsyncUSTradingContext


@dataclass(slots=True)
class GatewayHolding:
    symbol: str
    quantity: int


class BaseStrategyGateway:
    market: str

    def __init__(self, *, mode: str, account_name: str):
        self.mode = mode
        self.account_name = account_name
        self._context = None
        self._trader = None

    async def __aenter__(self):
        self._context = self._build_context()
        self._trader = await self._context.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._context is not None:
            await self._context.__aexit__(exc_type, exc_val, exc_tb)
        self._context = None
        self._trader = None

    @property
    def trader(self):
        if self._trader is None:
            raise RuntimeError("Strategy gateway is not active")
        return self._trader

    @property
    def account_id(self) -> str:
        return getattr(self.trader, "account_key", self.account_name)

    def _build_context(self):
        raise NotImplementedError

    async def get_available_amount(self) -> float:
        raise NotImplementedError

    async def get_holdings(self) -> list[GatewayHolding]:
        raise NotImplementedError

    async def buy(self, symbol: str, amount: float, *, limit_price: float | None = None) -> dict[str, Any]:
        raise NotImplementedError

    async def sell(self, symbol: str, *, limit_price: float | None = None) -> dict[str, Any]:
        raise NotImplementedError


class KRStrategyGateway(BaseStrategyGateway):
    market = "KR"

    def _build_context(self):
        return AsyncTradingContext(mode=self.mode, account_name=self.account_name)

    async def get_available_amount(self) -> float:
        summary = self.trader.get_account_summary() or {}
        return float(summary.get("available_amount", 0))

    async def get_holdings(self) -> list[GatewayHolding]:
        portfolio = self.trader.get_portfolio()
        return [
            GatewayHolding(symbol=item["stock_code"], quantity=int(item.get("quantity", 0)))
            for item in portfolio
            if int(item.get("quantity", 0)) > 0
        ]

    async def buy(self, symbol: str, amount: float, *, limit_price: float | None = None) -> dict[str, Any]:
        normalized_limit = None if limit_price in (None, 0) else int(limit_price)
        return await self.trader.async_buy_stock(
            stock_code=symbol,
            buy_amount=int(amount),
            limit_price=normalized_limit,
        )

    async def sell(self, symbol: str, *, limit_price: float | None = None) -> dict[str, Any]:
        normalized_limit = None if limit_price in (None, 0) else int(limit_price)
        return await self.trader.async_sell_stock(stock_code=symbol, limit_price=normalized_limit)


class USStrategyGateway(BaseStrategyGateway):
    market = "US"

    def _build_context(self):
        return AsyncUSTradingContext(mode=self.mode, account_name=self.account_name)

    async def get_available_amount(self) -> float:
        summary = self.trader.get_account_summary() or {}
        return float(summary.get("available_amount", 0))

    async def get_holdings(self) -> list[GatewayHolding]:
        portfolio = self.trader.get_portfolio()
        return [
            GatewayHolding(symbol=item["ticker"].upper(), quantity=int(item.get("quantity", 0)))
            for item in portfolio
            if int(item.get("quantity", 0)) > 0
        ]

    async def buy(self, symbol: str, amount: float, *, limit_price: float | None = None) -> dict[str, Any]:
        return await self.trader.async_buy_stock(
            ticker=symbol,
            buy_amount=float(amount),
            limit_price=limit_price,
        )

    async def sell(self, symbol: str, *, limit_price: float | None = None) -> dict[str, Any]:
        return await self.trader.async_sell_stock(ticker=symbol, limit_price=limit_price)


class StrategyGatewayFactory:
    """Create normalized single-account strategy gateways."""

    def __init__(self, *, mode: str):
        self.mode = mode

    def create(self, *, market: str, account_name: str) -> BaseStrategyGateway:
        if market == "KR":
            return KRStrategyGateway(mode=self.mode, account_name=account_name)
        if market == "US":
            return USStrategyGateway(mode=self.mode, account_name=account_name)
        raise ValueError(f"Unsupported market '{market}'")
