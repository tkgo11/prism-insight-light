"""Market-hours helpers for the standalone subscriber."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import holidays
import pandas_market_calendars as mcal
import pytz
import yaml


KST = pytz.timezone("Asia/Seoul")
US_EASTERN = pytz.timezone("US/Eastern")
KR_MARKET_OPEN = time(9, 0)
KR_NEXT_OPEN = time(9, 5)
US_MARKET_OPEN = time(9, 30)
US_NEXT_OPEN = time(9, 35)
US_MARKET_CLOSE = time(16, 0)


def _config_path() -> Path:
    trading_dir = Path(__file__).parent
    candidate = trading_dir / "config" / "kis_devlp.yaml"
    if candidate.exists():
        return candidate
    return trading_dir / "config" / "kis_devlp.yaml.example"


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
    valid_days = calendar.valid_days(start_date=day, end_date=day)
    return not valid_days.empty


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
        if not _is_us_trading_day(current.date()):
            return False
        current_time = current.time()
        return US_MARKET_OPEN <= current_time <= US_MARKET_CLOSE

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
