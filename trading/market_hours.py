"""Market-hours helpers for the standalone subscriber."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo
import importlib
import importlib.util
from pathlib import Path

from .config_paths import active_kis_config_path

holidays_spec = importlib.util.find_spec("holidays")
if holidays_spec is not None:
    holidays = importlib.import_module("holidays")
else:  # pragma: no cover - minimal test environment fallback
    class _HolidaysFallback:
        @staticmethod
        def country_holidays(country):
            raise RuntimeError(
                "The 'holidays' dependency is required for fail-closed KR market-hours checks"
            )

    holidays = _HolidaysFallback()

mcal_spec = importlib.util.find_spec("pandas_market_calendars")
if mcal_spec is not None:
    mcal = importlib.import_module("pandas_market_calendars")
else:  # pragma: no cover - minimal test environment fallback
    class _MarketCalendarFallback:
        @staticmethod
        def get_calendar(name):
            raise RuntimeError(
                "The 'pandas-market-calendars' dependency is required for fail-closed US market-hours checks"
            )

    mcal = _MarketCalendarFallback()

pytz_spec = importlib.util.find_spec("pytz")
if pytz_spec is not None:
    pytz = importlib.import_module("pytz")
else:  # pragma: no cover - minimal test environment fallback
    class _FixedTimezone(tzinfo):
        def __init__(self, name, offset_hours):
            self._name = name
            self._offset = timedelta(hours=offset_hours)

        def utcoffset(self, dt):
            return self._offset

        def dst(self, dt):
            return timedelta(0)

        def tzname(self, dt):
            return self._name

        def localize(self, dt):
            return dt.replace(tzinfo=self)

    class _PytzFallback:
        BaseTzInfo = tzinfo

        @staticmethod
        def timezone(name):
            offsets = {"Asia/Seoul": 9, "US/Eastern": -5}
            return _FixedTimezone(name, offsets.get(name, 0))

    pytz = _PytzFallback()
from . import yaml_compat as yaml


KST = pytz.timezone("Asia/Seoul")
US_EASTERN = pytz.timezone("US/Eastern")
KR_MARKET_OPEN = time(9, 0)
KR_NEXT_OPEN = time(9, 5)
US_NEXT_OPEN = time(9, 35)

# Broker-supported order windows while the regular market is closed.
# KR supports after-hours closing-price orders followed by reserved orders.
KR_CLOSING_ORDER_START = time(15, 40)
KR_CLOSING_ORDER_END = time(16, 0)
KR_RESERVED_EVENING_START = time(16, 0)
KR_RESERVED_EVENING_END = time(23, 40)
KR_RESERVED_MORNING_START = time(0, 10)
KR_RESERVED_MORNING_END = time(7, 30)

# KIS overseas reserved-order window is defined in Korea time.
US_RESERVED_ORDER_START = time(10, 0)
US_RESERVED_ORDER_END = time(23, 20)
US_RESERVED_MAINTENANCE_START = time(16, 30)
US_RESERVED_MAINTENANCE_END = time(16, 45)


def _config_path() -> Path:
    return active_kis_config_path()


def get_trading_mode() -> str:
    try:
        with open(_config_path(), encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    except FileNotFoundError:
        return "real"
    return str(config.get("default_mode", "real")).strip().lower() or "real"


def _coerce_now(now: datetime | None, tz: pytz.BaseTzInfo) -> datetime:
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return tz.localize(now)
    return now.astimezone(tz)


def _is_kr_trading_day(day: date) -> bool:
    return day.weekday() < 5 and day not in holidays.country_holidays("KR")


def _next_kr_trading_day(day: date) -> date:
    candidate = day
    while not _is_kr_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def _is_us_trading_day(day: date) -> bool:
    calendar = mcal.get_calendar("NYSE")
    return not calendar.schedule(start_date=day, end_date=day).empty


def _next_us_trading_day(day: date) -> date:
    candidate = day
    while not _is_us_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def is_market_open(market: str, *, now: datetime | None = None) -> bool:
    market = market.upper()
    if market == "KR":
        current = _coerce_now(now, KST)
        if not _is_kr_trading_day(current.date()):
            return False
        current_time = current.time()
        return KR_MARKET_OPEN <= current_time <= time(15, 30)

    if market == "US":
        current = _coerce_now(now, US_EASTERN)
        calendar = mcal.get_calendar("NYSE")
        schedule = calendar.schedule(start_date=current.date(), end_date=current.date())
        if schedule.empty:
            return False
        market_open = schedule.iloc[0]["market_open"].to_pydatetime()
        market_close = schedule.iloc[0]["market_close"].to_pydatetime()
        current_utc = current.astimezone(timezone.utc)
        return market_open <= current_utc < market_close

    raise ValueError(f"Unsupported market '{market}'")


def is_off_hours_order_available(market: str, *, now: datetime | None = None) -> bool:
    """Return whether KIS accepts a non-regular-session order right now.

    This is intentionally separate from :func:`is_market_open`.  In real mode,
    the dispatcher may submit an after-hours or reserved order when this returns
    true.  When it returns false, the signal must be durably queued instead of
    being acknowledged and discarded.
    """

    market = market.upper()
    current = _coerce_now(now, KST)
    current_time = current.time()

    if market == "KR":
        return (
            KR_CLOSING_ORDER_START <= current_time <= KR_CLOSING_ORDER_END
            or KR_RESERVED_EVENING_START < current_time <= KR_RESERVED_EVENING_END
            or KR_RESERVED_MORNING_START <= current_time <= KR_RESERVED_MORNING_END
        )

    if market == "US":
        if US_RESERVED_MAINTENANCE_START <= current_time <= US_RESERVED_MAINTENANCE_END:
            return False
        return US_RESERVED_ORDER_START <= current_time <= US_RESERVED_ORDER_END

    raise ValueError(f"Unsupported market '{market}'")


def next_market_open(market: str, *, now: datetime | None = None) -> datetime:
    market = market.upper()
    if market == "KR":
        current = _coerce_now(now, KST)
        base_day = current.date()
        if _is_kr_trading_day(base_day) and current.time() < KR_NEXT_OPEN:
            next_day = base_day
        else:
            next_day = _next_kr_trading_day(base_day + timedelta(days=1))
        return KST.localize(datetime.combine(next_day, KR_NEXT_OPEN)).astimezone(timezone.utc)

    if market == "US":
        current = _coerce_now(now, US_EASTERN)
        base_day = current.date()
        if _is_us_trading_day(base_day) and current.time() < US_NEXT_OPEN:
            next_day = base_day
        else:
            next_day = _next_us_trading_day(base_day + timedelta(days=1))
        return US_EASTERN.localize(datetime.combine(next_day, US_NEXT_OPEN)).astimezone(timezone.utc)

    raise ValueError(f"Unsupported market '{market}'")
