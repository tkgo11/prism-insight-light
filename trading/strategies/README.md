# Trading Strategies

This package contains opt-in trading strategies that can replace the default
signal execution path for specific signal types. Use the existing
`balance_split` strategy as the reference implementation when adding another
strategy.

## Built-in strategies

- `balance_split`: BUY with available cash (all of it by default).
- `balanced_risk`: recommended aggressive BUY/SELL strategy that never filters valid trade signals by default.
- `score_weighted`: BUY with score weighting; missing/low scores still execute.
- `score_risk`: use stop-loss risk sizing when available, otherwise use the broker default BUY size.
- `risk_bracket`: BUY from a risk budget derived from `price` and `stop_loss`, with optional bracket metadata persistence.
- `profit_ladder`: SELL fully by default, with optional configured profit bands.
- `protective_exit`: SELL fully by default, with optional staged exits.
- `stop_loss_sell`: SELL at the incoming `stop_loss` price by default, using `price` when configured as a fallback or when a triggered US stop-loss sell needs a marketable limit.
- `limit_buffer`: BUY/SELL using a buffered limit price around the signal price.
- `cooldown`: optional duplicate guard with no protected signal types by default.
- `event_risk_off`: optional event guard with no risk-off events and a 1.0 BUY multiplier by default.

## How to add a new strategy

1. **Create a strategy module** under `trading/strategies/` with a clear file
   name, for example `my_strategy.py`.
2. **Define a stable strategy name constant** in that module, such as
   `MY_STRATEGY = "my_strategy"`. This value is what users put in
   `signal_strategy.name` in `trading/config/kis_devlp.yaml`.
3. **Add a config dataclass** with a `from_mapping()` class method. The method
   should:
   - Return `None` when the mapping is empty or when `name` does not match the
     strategy constant.
   - Validate strategy-specific options and raise `ValueError` for invalid
     values.
4. **Implement the strategy class** with an async `execute(signal, *,
   trading_mode)` method. It should return a small execution result object with
   at least `status` and `message` fields so the dispatcher can turn it into a
   `DispatchResult`.
5. **Keep market-specific broker calls inside the strategy** when the strategy
   changes order sizing, price handling, reservations, or other execution
   behavior. Use `USStockTrading` for US orders and `AsyncTradingContext` for KR
   orders, following `balance_split.py`.
6. **Export the new types** from `trading/strategies/__init__.py` so callers can
   import them from `trading.strategies`.
7. **Wire the strategy into `TradeDispatcher`** by loading the new config in
   `trading/dispatch.py` and returning the strategy from `_resolve_buy_strategy()`
   or a new resolver for the signal type it supports.
8. **Document the user-facing config** in
   `trading/config/kis_devlp.yaml.example`, including the strategy name and each
   supported option.
9. **Update the user strategy guide** in `trading/STRATEGIES.md`. Every new
   strategy must be added there so users can understand it without reading code.
10. **Add tests** that cover config parsing, disabled or mismatched strategy
   names, successful execution, rejection paths, and dispatcher routing.

Strategies should be opt-in and should preserve the legacy execution path when
`signal_strategy.name` is empty or names a different strategy.
