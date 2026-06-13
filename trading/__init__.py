"""Trading package public API with lazy imports for optional runtime dependencies."""

from __future__ import annotations

_DISPATCH_EXPORTS = {"DispatchResult", "SignalDispatcher", "TradeDispatcher"}
_MARKET_HOURS_EXPORTS = {"get_trading_mode", "is_market_open", "next_market_open"}
_QUEUE_EXPORTS = {"OffHoursOrderQueue", "OffHoursQueue"}
_SCHEMA_EXPORTS = {
    "SignalMessage",
    "SignalValidationError",
    "TradingSignal",
    "parse_signal",
    "parse_signal_bytes",
    "parse_signal_payload",
}
_TELEGRAM_EXPORTS = {
    "DEFAULT_TELEGRAM_CHANNEL_URL",
    "ParsedTelegramSignal",
    "TelegramChannelPost",
    "TelegramFetchError",
    "build_channel_preview_url",
    "extract_channel_posts",
    "fetch_channel_posts",
    "fetch_signal_messages",
    "normalize_channel_handle",
    "parse_signal_post",
    "parse_signal_text",
}

__all__ = sorted(_DISPATCH_EXPORTS | _MARKET_HOURS_EXPORTS | _QUEUE_EXPORTS | _SCHEMA_EXPORTS | _TELEGRAM_EXPORTS)


def __getattr__(name: str):
    if name in _DISPATCH_EXPORTS:
        from . import dispatch

        return getattr(dispatch, name)
    if name in _MARKET_HOURS_EXPORTS:
        from . import market_hours

        return getattr(market_hours, name)
    if name in _QUEUE_EXPORTS:
        from . import off_hours_queue

        return getattr(off_hours_queue, name)
    if name in _SCHEMA_EXPORTS:
        from . import schema

        return getattr(schema, name)
    if name in _TELEGRAM_EXPORTS:
        from . import telegram_fetch

        return getattr(telegram_fetch, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
