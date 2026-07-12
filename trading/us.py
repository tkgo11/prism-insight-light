"""
US Stock Trading Module (KIS Overseas Stock API)

Provides:
- Fixed amount purchase per stock
- Market price buy/sell
- Full liquidation sell
- Portfolio management

Key differences from Korean domestic trading:
- API endpoints: /uapi/overseas-stock/ instead of /uapi/domestic-stock/
- Exchange codes: NASD (NASDAQ), NYSE (NYSE), AMEX (AMEX)
- TR IDs are different for overseas trading
- Currency: USD
- Market hours: 09:30-16:00 EST (23:30-06:00 KST next day)
"""

import asyncio
import datetime
import importlib
import importlib.util
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Dict, List, Any

from . import yaml_compat as yaml
pytz_spec = importlib.util.find_spec("pytz")
if pytz_spec is not None:
    pytz = importlib.import_module("pytz")
else:  # pragma: no cover - minimal test environment fallback
    class _FixedTimezone(datetime.tzinfo):
        def __init__(self, name, offset_hours):
            self._name = name
            self._offset = datetime.timedelta(hours=offset_hours)

        def utcoffset(self, dt):
            return self._offset

        def dst(self, dt):
            return datetime.timedelta(0)

        def tzname(self, dt):
            return self._name

        def localize(self, dt):
            return dt.replace(tzinfo=self)

    class _PytzFallback:
        @staticmethod
        def timezone(name):
            offsets = {"Asia/Seoul": 9, "US/Eastern": -5}
            return _FixedTimezone(name, offsets.get(name, 0))

    pytz = _PytzFallback()

# Path to directory where current file is located
TRADING_DIR = Path(__file__).parent
PROJECT_ROOT = TRADING_DIR.parent

from . import kis_auth as ka
from .buy_sizing import build_buy_sizing, resolve_buy_amount

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load configuration file (use same config as domestic)
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
if not CONFIG_FILE.exists():
    CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml.example"
with open(CONFIG_FILE, encoding="UTF-8") as f:
    _cfg = yaml.safe_load(f)

# Timezones
US_EASTERN = pytz.timezone('US/Eastern')
KST = pytz.timezone('Asia/Seoul')


# =============================================================================
# Safe Type Conversion Helpers (handle empty strings from KIS API)
# =============================================================================
def _safe_float(value, default: float = 0.0) -> float:
    """
    Safely convert value to float, handling empty strings and None.

    KIS API sometimes returns empty string '' instead of 0 for price fields,
    which causes 'could not convert string to float' errors.

    Args:
        value: Value to convert (can be str, int, float, None, or '')
        default: Default value if conversion fails

    Returns:
        float: Converted value or default
    """
    if value is None or value == '':
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _safe_int(value, default: int = 0) -> int:
    """
    Safely convert value to int, handling empty strings and None.

    Args:
        value: Value to convert (can be str, int, float, None, or '')
        default: Default value if conversion fails

    Returns:
        int: Converted value or default
    """
    if value is None or value == '':
        return default
    try:
        return int(float(value))  # Handle "123.0" string case
    except (ValueError, TypeError):
        return default


@dataclass(frozen=True)
class AutoExchangeConfig:
    """Opt-in KRW-to-USD auto-exchange settings for US stock buys."""

    enabled: bool = False
    buffer_percent: float = 2.0
    max_krw: float | None = None
    min_shortfall_usd: float = 1.0


def _cfg_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return default


def _cfg_positive_float(value: Any, default: float | None = None) -> float | None:
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def build_auto_exchange_config(account_config: dict[str, Any] | None) -> AutoExchangeConfig:
    account_config = account_config or {}
    enabled = _cfg_bool(
        account_config.get("auto_exchange_usd_on_buy"),
        _cfg_bool(_cfg.get("auto_exchange_usd_on_buy"), False),
    )
    buffer_percent = _cfg_positive_float(
        account_config.get("auto_exchange_buffer_percent"),
        _cfg_positive_float(_cfg.get("auto_exchange_buffer_percent"), 2.0),
    )
    max_krw = _cfg_positive_float(
        account_config.get("max_auto_exchange_krw"),
        _cfg_positive_float(_cfg.get("max_auto_exchange_krw"), None),
    )
    min_shortfall_usd = _cfg_positive_float(
        account_config.get("auto_exchange_min_shortfall_usd"),
        _cfg_positive_float(_cfg.get("auto_exchange_min_shortfall_usd"), 1.0),
    )
    return AutoExchangeConfig(
        enabled=enabled,
        buffer_percent=float(buffer_percent if buffer_percent is not None else 2.0),
        max_krw=max_krw,
        min_shortfall_usd=float(min_shortfall_usd if min_shortfall_usd is not None else 1.0),
    )

# Exchange code mapping (for trading/portfolio APIs using OVRS_EXCG_CD)
EXCHANGE_CODES = {
    "NASDAQ": "NASD",
    "NYSE": "NYSE",
    "AMEX": "AMEX",
    "NASD": "NASD",  # Allow direct use
}

# Price query API uses shorter exchange codes (EXCD parameter)
PRICE_EXCHANGE_CODES = {
    "NASD": "NAS",
    "NYSE": "NYS",
    "AMEX": "AMS",
    "NAS": "NAS",
    "NYS": "NYS",
    "AMS": "AMS",
}

# Common NASDAQ stocks for exchange detection
NASDAQ_TICKERS = {
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA",
    "AVGO", "COST", "ADBE", "CSCO", "PEP", "NFLX", "INTC", "AMD",
    "QCOM", "TXN", "HON", "CMCSA", "SBUX", "GILD", "MDLZ", "ISRG",
    "VRTX", "REGN", "ATVI", "ADP", "BKNG", "CHTR", "LRCX", "MU",
    "KLAC", "SNPS", "CDNS", "MRVL", "PANW", "CRWD", "ZS", "DDOG"
}


def get_exchange_code(ticker: str) -> str:
    """
    Determine the exchange code for a given ticker.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Exchange code (NASD, NYSE, AMEX)
    """
    # Simple heuristic - most tech stocks are on NASDAQ
    # In production, you'd want to look this up from a database or API
    ticker_upper = ticker.upper()

    if ticker_upper in NASDAQ_TICKERS:
        return "NASD"

    # Default to NYSE for unknown tickers (can be overridden)
    return "NYSE"


class USStockTrading:
    """US Stock Trading class using KIS Overseas Stock API"""

    # Default buy amount per stock (in USD)
    DEFAULT_BUY_AMOUNT = _cfg.get("default_unit_amount_usd", 100)  # $100 default
    # Auto trading enabled flag
    AUTO_TRADING = _cfg.get("auto_trading", True)
    # Default trading environment
    DEFAULT_MODE = _cfg.get("default_mode", "demo")

    def __init__(
        self,
        mode: str = None,
        buy_amount: float = None,
        auto_trading: bool = None,
        account_name: str = None,
        account_index: int = None,
        product_code: str = "01",
    ):
        """
        Initialize US Stock Trading

        Args:
            mode: 'demo' (simulated) or 'real' (real trading)
            buy_amount: Buy amount per stock in USD (default: from config)
            auto_trading: Whether to execute auto trading
        """
        from .modes import normalize_trading_mode

        self.mode = normalize_trading_mode(self.DEFAULT_MODE if mode is None else mode)
        self.env = "vps" if self.mode == "demo" else "prod"
        self.auto_trading = auto_trading if auto_trading is not None else self.AUTO_TRADING
        self.account_index = account_index
        self.account_config = ka.resolve_account(
            svr=self.env,
            product=str(product_code),
            account_name=account_name,
            account_index=account_index,
            market="us",
        )
        self.account_name = self.account_config["name"]
        self.account_key = self.account_config["account_key"]
        self.account_index = account_index
        self.product_code = self.account_config["product"]
        default_buy_amount = float(self.account_config.get("buy_amount_usd") or self.DEFAULT_BUY_AMOUNT)
        self.buy_amount = buy_amount if buy_amount is not None else default_buy_amount
        self.buy_sizing = build_buy_sizing(
            fixed_amount=self.buy_amount,
            asset_percent=None if buy_amount is not None else self.account_config.get("buy_percent_usd"),
        )
        self.auto_exchange = build_auto_exchange_config(self.account_config)

        # Authentication
        ka.auth(
            svr=self.env,
            product=self.product_code,
            account_key=self.account_key,
        )

        try:
            self.trenv = ka.getTREnv()
        except RuntimeError as e:
            print("❌ KIS API authentication failed!")
            print(f"Mode: {self.mode}, Error: {e}")
            print("📋 Please check kis_devlp.yaml settings.")
            raise RuntimeError(f"{self.mode} mode authentication failed") from e

        # Async setup
        self._global_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(3)
        self._stock_locks = {}

        logger.info(f"USStockTrading initialized (Async Enabled)")
        logger.info(f"Mode: {mode}, Buy Amount: ${self.buy_amount:,.2f} USD")
        logger.info(f"Account: {self.account_name} ({ka.mask_account_number(self.trenv.my_acct)}-{self.trenv.my_prod})")

    def _activate_account(self):
        """Ensure the shared KIS environment matches this trader's account."""
        ka.changeTREnv(
            self.trenv.my_token,
            svr=self.env,
            product=self.trenv.my_prod,
            account_key=self.account_key,
        )

    def _request(self, api_url: str, tr_id: str, params: Dict[str, Any], **kwargs):
        with ka.get_trading_env_lock():
            self._activate_account()
            response = ka._url_fetch(api_url, tr_id, "", params, **kwargs)
            try:
                self.trenv = ka.getTREnv()
            except RuntimeError:
                logger.debug("KIS trading environment unavailable after request; keeping existing trader environment")
            return response

    def get_current_price(self, ticker: str, exchange: str = None) -> Optional[Dict[str, Any]]:
        """
        Get current market price for US stock

        Args:
            ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
            exchange: Exchange code (NASD, NYSE, AMEX) - auto-detected if not provided

        Returns:
            {
                'ticker': 'AAPL',
                'stock_name': 'APPLE INC',
                'current_price': 185.50,
                'change_rate': 1.25,
                'volume': 45000000,
                'exchange': 'NASD'
            }
        """
        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        api_url = "/uapi/overseas-price/v1/quotations/price"
        tr_id = "HHDFS00000300"

        # Price API uses shorter exchange codes (NAS/NYS/AMS)
        price_excd = PRICE_EXCHANGE_CODES.get(exchange, exchange)

        params = {
            "AUTH": "",
            "EXCD": price_excd,
            "SYMB": ticker.upper()
        }

        try:
            res = self._request(api_url, tr_id, params)

            if res.isOK():
                data = res.getBody().output

                # Use safe conversion helpers to handle empty strings from API
                current_price = _safe_float(data.get('last'))

                # When market is closed, 'last' is empty; fall back to 'base' (previous day close)
                if current_price <= 0:
                    base_price = _safe_float(data.get('base'))
                    if base_price > 0:
                        logger.info(f"[{ticker}] Market closed - 'last' empty, using base price ${base_price:.2f}")
                        current_price = base_price
                    else:
                        logger.warning(f"[{ticker}] Invalid price received: last='{data.get('last')}', base='{data.get('base')}'")
                        return None

                result = {
                    'ticker': ticker.upper(),
                    'stock_name': data.get('name', ''),
                    'current_price': current_price,
                    'change_rate': _safe_float(data.get('rate')),
                    'volume': _safe_int(data.get('tvol')),
                    'exchange': exchange
                }

                logger.info(f"[{ticker}] Current price: ${result['current_price']:.2f} ({result['change_rate']:+.2f}%)")
                return result
            else:
                logger.error(f"Price query failed: {res.getErrorCode()} - {res.getErrorMessage()}")
                return None

        except Exception as e:
            logger.error(f"Error getting price: {str(e)}")
            return None

    def _resolve_buy_amount(self, buy_amount: float | None = None) -> float:
        if buy_amount is not None:
            return float(buy_amount)
        return resolve_buy_amount(
            self.buy_sizing,
            account_summary=self.get_account_summary() if self.buy_sizing.uses_asset_percent else None,
            fallback_amount=float(self.buy_amount),
            currency="USD",
        )

    def get_overseas_buyable_amount(self, ticker: str, price: float, exchange: str = None) -> Dict[str, Any]:
        """Return KIS overseas buyability fields, including after-exchange amount.

        KIS exposes ``echm_af_ord_psbl_amt`` (amount orderable after exchange),
        which is the safest available API surface for opt-in auto-exchange because
        the actual currency conversion is performed by KIS as part of the overseas
        buying-power calculation/order flow rather than by this bot placing a
        separate, poorly documented FX order.
        """
        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        api_url = "/uapi/overseas-stock/v1/trading/inquire-psamount"
        tr_id = "TTTS3007R" if self.mode == "real" else "VTTS3007R"
        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "OVRS_ORD_UNPR": f"{price:.8f}".rstrip("0").rstrip("."),
            "ITEM_CD": ticker.upper(),
        }

        res = self._request(api_url, tr_id, params)
        if not res.isOK():
            error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
            logger.warning("[%s] Overseas buyable amount inquiry failed: %s", ticker, error_msg)
            return {}

        output = getattr(res.getBody(), "output", {})
        if isinstance(output, list):
            output = output[0] if output else {}
        return output or {}

    def _resolve_orderable_usd(self, ticker: str, requested_amount: float, price: float, exchange: str) -> tuple[float, Dict[str, Any]]:
        """Resolve USD that may be submitted, optionally including KIS auto-exchange buying power."""
        summary = self.get_account_summary() or {}
        usd_cash = _safe_float(summary.get("available_amount"), _safe_float(summary.get("usd_cash")))
        info = {"usd_cash": usd_cash, "auto_exchange_used": False}
        auto_exchange = getattr(self, "auto_exchange", AutoExchangeConfig(enabled=False))

        should_query_buyable = hasattr(self, "trenv") and (
            auto_exchange.enabled or requested_amount <= usd_cash
        )
        buyable = {}
        if should_query_buyable:
            try:
                buyable = self.get_overseas_buyable_amount(ticker, price, exchange)
            except Exception as exc:
                logger.warning("[%s] Overseas buyable amount inquiry raised; falling back to account cash: %s", ticker, exc)
                buyable = {}
        has_current_orderable = bool(buyable) and buyable.get("ord_psbl_frcr_amt") not in (None, "")
        current_orderable = _safe_float(buyable.get("ord_psbl_frcr_amt")) if has_current_orderable else 0.0
        after_exchange_orderable = (
            _safe_float(
                buyable.get("echm_af_ord_psbl_amt"),
                _safe_float(buyable.get("ovrs_ord_psbl_amt")),
            )
            if buyable
            else 0.0
        )

        cash_orderable = usd_cash
        if has_current_orderable:
            cash_orderable = min(cash_orderable, current_orderable) if cash_orderable > 0 else current_orderable

        if cash_orderable >= requested_amount:
            one_share_shortfall = price - cash_orderable
            if (
                auto_exchange.enabled
                and requested_amount < price
                and cash_orderable < price
                and one_share_shortfall >= auto_exchange.min_shortfall_usd
                and after_exchange_orderable > cash_orderable
            ):
                exchange_rate = _safe_float(buyable.get("exrt"), _safe_float(summary.get("exchange_rate")))
                orderable_usd = after_exchange_orderable
                if auto_exchange.max_krw is not None and exchange_rate > 0:
                    max_exchange_usd = auto_exchange.max_krw / exchange_rate
                    orderable_usd = min(orderable_usd, cash_orderable + max_exchange_usd)
                resolved = min(max(requested_amount, price), orderable_usd)
                if resolved > requested_amount:
                    info.update(
                        {
                            "auto_exchange_used": resolved > cash_orderable,
                            "exchange_rate": exchange_rate,
                            "orderable_after_exchange_usd": after_exchange_orderable,
                        }
                    )
                    logger.info(
                        "[%s] Auto-exchange enabled: USD cash %.2f covers requested %.2f but not one share at %.2f; using %.2f",
                        ticker,
                        cash_orderable,
                        requested_amount,
                        price,
                        resolved,
                    )
                    return resolved, info
            return requested_amount, info

        if not auto_exchange.enabled:
            if cash_orderable > 0 or has_current_orderable:
                logger.info(
                    "[%s] Capping buy amount %.2f USD to KIS orderable USD %.2f; auto exchange is disabled",
                    ticker,
                    requested_amount,
                    cash_orderable,
                )
                return cash_orderable, info
            return requested_amount, info

        shortfall = requested_amount - cash_orderable
        if shortfall < auto_exchange.min_shortfall_usd:
            return min(requested_amount, cash_orderable), info

        if not buyable:
            return min(requested_amount, cash_orderable) if cash_orderable > 0 else requested_amount, info

        exchange_rate = _safe_float(buyable.get("exrt"), _safe_float(summary.get("exchange_rate")))
        orderable_usd = max(cash_orderable, after_exchange_orderable)

        if auto_exchange.max_krw is not None and exchange_rate > 0:
            max_exchange_usd = auto_exchange.max_krw / exchange_rate
            orderable_usd = min(orderable_usd, cash_orderable + max_exchange_usd)

        if orderable_usd <= cash_orderable:
            if cash_orderable > 0 or has_current_orderable:
                return min(requested_amount, cash_orderable), info
            return requested_amount, info

        resolved = min(requested_amount, orderable_usd)
        info.update(
            {
                "auto_exchange_used": resolved > cash_orderable,
                "exchange_rate": exchange_rate,
                "orderable_after_exchange_usd": after_exchange_orderable,
            }
        )
        if resolved < requested_amount:
            logger.info(
                "[%s] Capping buy amount %.2f USD to KIS after-exchange buying power %.2f USD",
                ticker,
                requested_amount,
                resolved,
            )
        elif resolved > cash_orderable:
            logger.info(
                "[%s] Auto-exchange enabled: USD cash %.2f, KIS after-exchange buying power %.2f, requested %.2f",
                ticker,
                cash_orderable,
                after_exchange_orderable,
                requested_amount,
            )
        return resolved, info

    def calculate_buy_quantity(self, ticker: str, buy_amount: float = None,
                               exchange: str = None) -> int:
        """
        Calculate buyable quantity

        Args:
            ticker: Stock ticker symbol
            buy_amount: Buy amount in USD (default: class setting)
            exchange: Exchange code

        Returns:
            Buyable quantity (0 if cannot buy)
        """
        amount, _ = self._calculate_buy_quantity_inputs(ticker, buy_amount, exchange)
        return amount

    def _calculate_buy_quantity_inputs(self, ticker: str, buy_amount: float = None,
                                       exchange: str = None) -> tuple[int, Dict[str, Any]]:
        amount = self._resolve_buy_amount(buy_amount)

        price_info = self.get_current_price(ticker, exchange)
        if not price_info:
            return 0, {"requested_amount": amount}

        current_price = price_info['current_price']
        if current_price <= 0:
            logger.error(f"[{ticker}] Invalid current price: ${current_price}")
            return 0, {"requested_amount": amount, "current_price": current_price}

        resolved_amount, exchange_info = self._resolve_orderable_usd(
            ticker,
            amount,
            current_price,
            EXCHANGE_CODES.get(exchange.upper(), exchange) if exchange else get_exchange_code(ticker),
        )

        quantity = math.floor(resolved_amount / current_price)

        if quantity == 0:
            logger.warning(f"[{ticker}] Price ${current_price:.2f} > Amount ${resolved_amount:.2f} - Cannot buy")
        else:
            total = quantity * current_price
            logger.info(f"[{ticker}] Buyable: {quantity} shares x ${current_price:.2f} = ${total:.2f}")

        info = {
            "requested_amount": amount,
            "resolved_amount": resolved_amount,
            "current_price": current_price,
            **exchange_info,
        }
        return quantity, info

    def buy_market_price(self, ticker: str, buy_amount: float = None,
                         exchange: str = None) -> Dict[str, Any]:
        """
        Market price buy for US stock

        Args:
            ticker: Stock ticker symbol
            buy_amount: Buy amount in USD
            exchange: Exchange code

        Returns:
            {
                'success': bool,
                'order_no': str,
                'ticker': str,
                'quantity': int,
                'message': str
            }
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        # Calculate buy quantity, including optional KIS after-exchange buying power
        buy_quantity, buy_info = self._calculate_buy_quantity_inputs(ticker, buy_amount, exchange)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Buy quantity is 0 (price higher than buy amount)'
            }

        # Execute buy order
        api_url = "/uapi/overseas-stock/v1/trading/order"

        # TR ID for overseas stock buy
        if self.mode == "real":
            tr_id = "TTTT1002U"  # Real overseas buy
        else:
            tr_id = "VTTT1002U"  # Demo overseas buy

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker.upper(),
            "ORD_QTY": str(buy_quantity),
            "OVRS_ORD_UNPR": "0",  # Market order price = 0
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "01"   # Market order (시장가)
        }

        try:
            res = self._request(api_url, tr_id, params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('ODNO', '')

                logger.info(f"[{ticker}] Market buy order success: {buy_quantity} shares, Order#: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'ticker': ticker,
                    'quantity': buy_quantity,
                    'auto_exchange_used': buy_info.get('auto_exchange_used', False),
                    'message': f'Market buy order completed ({buy_quantity} shares)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Buy order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': buy_quantity,
                    'auto_exchange_used': buy_info.get('auto_exchange_used', False),
                    'message': f'Buy order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during buy order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': buy_quantity,
                'message': f'Buy order error: {str(e)}'
            }

    def buy_limit_price(self, ticker: str, limit_price: float, buy_amount: float = None,
                        exchange: str = None) -> Dict[str, Any]:
        """
        Limit price buy for US stock

        Args:
            ticker: Stock ticker symbol
            limit_price: Limit price in USD
            buy_amount: Buy amount in USD
            exchange: Exchange code

        Returns:
            Order result dict
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'limit_price': limit_price,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        amount = self._resolve_buy_amount(buy_amount)
        amount, buy_info = self._resolve_orderable_usd(ticker, amount, limit_price, exchange)

        # Calculate quantity based on limit price
        buy_quantity = math.floor(amount / limit_price)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'limit_price': limit_price,
                'message': f'Buy quantity is 0 (limit ${limit_price:.2f} > amount ${amount:.2f})'
            }

        # Execute limit buy order
        api_url = "/uapi/overseas-stock/v1/trading/order"

        if self.mode == "real":
            tr_id = "TTTT1002U"
        else:
            tr_id = "VTTT1002U"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker.upper(),
            "ORD_QTY": str(buy_quantity),
            "OVRS_ORD_UNPR": f"{limit_price:.2f}",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"  # Limit order
        }

        try:
            res = self._request(api_url, tr_id, params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('ODNO', '')

                logger.info(f"[{ticker}] Limit buy order success: {buy_quantity} shares x ${limit_price:.2f}, Order#: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'ticker': ticker,
                    'quantity': buy_quantity,
                    'limit_price': limit_price,
                    'auto_exchange_used': buy_info.get('auto_exchange_used', False),
                    'message': f'Limit buy order completed ({buy_quantity} shares x ${limit_price:.2f})'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Limit buy order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': buy_quantity,
                    'limit_price': limit_price,
                    'auto_exchange_used': buy_info.get('auto_exchange_used', False),
                    'message': f'Buy order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during limit buy: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': buy_quantity,
                'limit_price': limit_price,
                'message': f'Buy order error: {str(e)}'
            }

    def get_holding_quantity(self, ticker: str) -> int:
        """
        Get holding quantity for a specific ticker

        Args:
            ticker: Stock ticker symbol

        Returns:
            Holding quantity (0 if not held)
        """
        portfolio = self.get_portfolio()

        for stock in portfolio:
            if stock['ticker'].upper() == ticker.upper():
                return stock['quantity']

        return 0

    def sell_all_market_price(self, ticker: str, exchange: str = None,
                              limit_price: float = None,
                              holding_quantity: Optional[int] = None) -> Dict[str, Any]:
        """
        Sell all holdings at current market price (limit order at current price).

        KIS TTTT1006U does not support ORD_DVSN "01" (market order) for sell.
        Valid values: 00=limit, 31=MOO, 32=LOO, 33=MOC, 34=LOC.
        We use ORD_DVSN "00" (limit) with the current price, which fills
        immediately when the market is open.

        Args:
            ticker: Stock ticker symbol
            exchange: Exchange code
            limit_price: Current price to use as limit price. If not provided,
                         fetched automatically.

        Returns:
            Order result dict
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        # Check holding quantity
        quantity = holding_quantity if holding_quantity is not None else self.get_holding_quantity(ticker)

        if quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'No holdings to sell'
            }

        # Fetch current price if not provided
        if not limit_price or limit_price <= 0:
            price_info = self.get_current_price(ticker, exchange)
            limit_price = price_info['current_price'] if price_info else 0.0
            if limit_price <= 0:
                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': 0,
                    'message': 'Failed to fetch current price for sell order'
                }

        # Execute sell order
        api_url = "/uapi/overseas-stock/v1/trading/order"

        if self.mode == "real":
            tr_id = "TTTT1006U"  # Real overseas sell
        else:
            tr_id = "VTTT1001U"  # Demo overseas sell

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker.upper(),
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": f"{limit_price:.2f}",  # KIS TTTT1006U: no market order (ORD_DVSN "01"), use limit at current price
            "ORD_SVR_DVSN_CD": "0",
            "SLL_TYPE": "00",  # Sell type
            "ORD_DVSN": "00"   # Limit order (지정가) — TTTT1006U does not support "01"
        }

        try:
            res = self._request(api_url, tr_id, params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('ODNO', '')

                logger.info(f"[{ticker}] Market sell order success: {quantity} shares, Order#: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'ticker': ticker,
                    'quantity': quantity,
                    'message': f'Market sell order completed ({quantity} shares)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Sell order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': quantity,
                    'message': f'Sell order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during sell order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': quantity,
                'message': f'Sell order error: {str(e)}'
            }

    def is_market_open(self) -> bool:
        """
        Check if US market is currently open

        Returns:
            True if market is open, False otherwise
        """
        now_et = datetime.datetime.now(US_EASTERN)
        current_time = now_et.time()

        # US market hours: 09:30 - 16:00 ET
        market_open = datetime.time(9, 30)
        market_close = datetime.time(16, 0)

        # Check if it's a weekday
        if now_et.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False

        return market_open <= current_time <= market_close

    def is_reserved_order_available(self) -> bool:
        """
        Check if reserved order is available (Korean time window)

        Reserved order available: 10:00 ~ 23:20 KST (winter) / 10:00 ~ 22:20 KST (summer)
        System maintenance: 16:30 ~ 16:45 KST (not available)

        Returns:
            True if reserved order can be placed, False otherwise
        """
        now_kst = datetime.datetime.now(KST)
        current_time = now_kst.time()

        # System maintenance window: 16:30 ~ 16:45
        if datetime.time(16, 30) <= current_time <= datetime.time(16, 45):
            return False

        # Reserved order window: 10:00 ~ 23:20 (using conservative winter time)
        resv_start = datetime.time(10, 0)
        resv_end = datetime.time(23, 20)

        return resv_start <= current_time <= resv_end

    def _reserved_window_closed(self, ticker: str, order_type: str, limit_price: float) -> Dict[str, Any]:
        """Return a standalone-safe failure when the US reserved-order window is closed."""
        logger.warning(
            "[%s] Cannot place reserved %s order outside the KIS reserved-order window",
            ticker,
            order_type,
        )
        return {
            'success': False,
            'order_no': None,
            'ticker': ticker,
            'quantity': 0,
            'limit_price': limit_price,
            'message': f'Reserved {order_type} order is unavailable outside the KIS reserved-order window.',
        }

    def buy_reserved_order(self, ticker: str, limit_price: float, buy_amount: float = None,
                           exchange: str = None) -> Dict[str, Any]:
        """
        Reserved order buy for US stock (executed at next market open)
        Reserved buy order - automatically executed at next market open

        Note: US reserved orders only support LIMIT orders (only limit price orders allowed)

        Args:
            ticker: Stock ticker symbol
            limit_price: Limit price in USD (REQUIRED - market order not supported)
            buy_amount: Buy amount in USD
            exchange: Exchange code

        Returns:
            Order result dict
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'limit_price': limit_price,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if not limit_price or limit_price <= 0:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'limit_price': 0,
                'message': 'Limit price is required for US reserved orders (market order not supported)'
            }

        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        amount = self._resolve_buy_amount(buy_amount)

        if not self.is_reserved_order_available():
            return self._reserved_window_closed(ticker, 'buy', limit_price)

        amount, buy_info = self._resolve_orderable_usd(ticker, amount, limit_price, exchange)

        # Calculate quantity based on limit price
        buy_quantity = math.floor(amount / limit_price)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'limit_price': limit_price,
                'message': f'Buy quantity is 0 (limit ${limit_price:.2f} > amount ${amount:.2f})'
            }

        # Reserved order API
        api_url = "/uapi/overseas-stock/v1/trading/order-resv"

        # TR ID for US reserved order buy
        if self.mode == "real":
            tr_id = "TTTT3014U"  # Real US reserved buy
        else:
            tr_id = "VTTT3014U"  # Demo US reserved buy

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker.upper(),
            "FT_ORD_QTY": str(int(buy_quantity)),  # Must be integer string for KIS API
            "FT_ORD_UNPR3": f"{limit_price:.2f}",
            "ORD_SVR_DVSN_CD": "0"
        }

        try:
            res = self._request(api_url, tr_id, params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('ODNO', '') or output.get('RSVN_ORD_SEQ', '')

                logger.info(f"[{ticker}] Reserved buy order success: {buy_quantity} shares x ${limit_price:.2f}, Order#: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'ticker': ticker,
                    'quantity': buy_quantity,
                    'limit_price': limit_price,
                    'order_type': 'reserved_limit',
                    'resolved_amount': amount,
                    'auto_exchange_used': buy_info.get('auto_exchange_used', False),
                    'message': f'Reserved buy order completed ({buy_quantity} shares x ${limit_price:.2f})'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Reserved buy order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': buy_quantity,
                    'limit_price': limit_price,
                    'message': f'Reserved buy order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during reserved buy order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': buy_quantity,
                'limit_price': limit_price,
                'message': f'Reserved buy order error: {str(e)}'
            }

    def sell_reserved_order(self, ticker: str, limit_price: float = None,
                            use_moo: bool = False, exchange: str = None,
                            holding_quantity: Optional[int] = None) -> Dict[str, Any]:
        """
        Reserved order sell for US stock (executed at next market open)
        Reserved sell order - automatically executed at next market open

        Note: US reserved sell orders support LIMIT or MOO (Market On Open)

        Args:
            ticker: Stock ticker symbol
            limit_price: Limit price in USD (required if use_moo is False)
            use_moo: Use Market On Open order (default: False)
            exchange: Exchange code

        Returns:
            Order result dict
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if not use_moo and (not limit_price or limit_price <= 0):
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Limit price is required for reserved sell (or use use_moo=True for Market On Open)'
            }

        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        if not self.is_reserved_order_available():
            return self._reserved_window_closed(ticker, 'sell', limit_price or 0)

        # Check holding quantity
        quantity = holding_quantity if holding_quantity is not None else self.get_holding_quantity(ticker)

        if quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'No holdings to sell'
            }

        # Reserved order API
        api_url = "/uapi/overseas-stock/v1/trading/order-resv"

        # TR ID for US reserved order sell
        if self.mode == "real":
            tr_id = "TTTT3016U"  # Real US reserved sell
        else:
            tr_id = "VTTT3016U"  # Demo US reserved sell

        # Set price based on order type
        if use_moo:
            order_price = "0"
            order_type_str = "MOO (Market On Open)"
        else:
            order_price = f"{limit_price:.2f}"
            order_type_str = f"Limit ${limit_price:.2f}"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker.upper(),
            "FT_ORD_QTY": str(int(quantity)),  # Must be integer string for KIS API
            "FT_ORD_UNPR3": order_price,
            "ORD_SVR_DVSN_CD": "0"
        }

        try:
            res = self._request(api_url, tr_id, params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('ODNO', '') or output.get('RSVN_ORD_SEQ', '')

                logger.info(f"[{ticker}] Reserved sell order success: {quantity} shares, {order_type_str}, Order#: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'ticker': ticker,
                    'quantity': quantity,
                    'limit_price': limit_price if not use_moo else None,
                    'order_type': 'reserved_moo' if use_moo else 'reserved_limit',
                    'message': f'Reserved sell order completed ({quantity} shares, {order_type_str})'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Reserved sell order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': quantity,
                    'message': f'Reserved sell order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during reserved sell order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': quantity,
                'message': f'Reserved sell order error: {str(e)}'
            }

    def smart_buy(self, ticker: str, buy_amount: float = None,
                  exchange: str = None, limit_price: float = None) -> Dict[str, Any]:
        """
        Smart buy - automatically choose best method based on market hours

        - Market open: Execute market price buy immediately
        - Market closed + limit_price provided: Place reserved order (limit price reserved order)
        - Market closed + no limit_price: Return error (reserved order requires limit price)

        Args:
            ticker: Stock ticker symbol
            buy_amount: Buy amount in USD
            exchange: Exchange code
            limit_price: Limit price for reserved order when market is closed

        Returns:
            Order result dict
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if self.is_market_open():
            # US stocks do NOT support market price buy (시장가 매수).
            # KIS API TTTT1002U ORD_DVSN "00" = limit price (지정가), not market price.
            # Sending OVRS_ORD_UNPR "0" causes APBK1507 error.
            # Always use limit price buy with the provided price.
            if limit_price and limit_price > 0:
                logger.info(f"[{ticker}] Market is open - executing limit buy @ ${limit_price:.2f}")
                return self.buy_limit_price(ticker, limit_price, buy_amount, exchange)
            else:
                logger.warning(f"[{ticker}] Market is open but no limit_price provided - cannot execute buy")
                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': 0,
                    'message': 'US stocks require limit_price for buy orders (no market price buy supported)'
                }
        else:
            # Market is closed - use reserved order if limit_price provided
            if limit_price and limit_price > 0:
                logger.info(f"[{ticker}] Market is closed - placing reserved order (limit: ${limit_price:.2f})")
                return self.buy_reserved_order(ticker, limit_price, buy_amount, exchange)
            else:
                logger.warning(f"[{ticker}] Market is closed and no limit_price provided - cannot place reserved order")
                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': 0,
                    'message': 'US market is closed. Provide limit_price for reserved order.'
                }

    def smart_sell_all(self, ticker: str, exchange: str = None,
                       limit_price: float = None, use_moo: bool = False,
                       holding_quantity: Optional[int] = None) -> Dict[str, Any]:
        """
        Smart sell - automatically choose best method based on market hours

        - Market open: Execute market price sell immediately
        - Market closed + limit_price provided: Place reserved order (limit price reserved order)
        - Market closed + use_moo=True: Place reserved MOO order (market price reserved order)
        - Market closed + no limit_price + no use_moo: Return error

        Args:
            ticker: Stock ticker symbol
            exchange: Exchange code
            limit_price: Limit price for reserved order when market is closed
            use_moo: Use Market On Open for reserved order (default: False)

        Returns:
            Order result dict
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if self.is_market_open():
            logger.info(f"[{ticker}] Market is open - executing market sell")
            return self.sell_all_market_price(
                ticker,
                exchange,
                limit_price=limit_price,
                holding_quantity=holding_quantity,
            )
        else:
            # Market is closed - use reserved order
            if limit_price and limit_price > 0:
                logger.info(f"[{ticker}] Market is closed - placing reserved sell order (limit: ${limit_price:.2f})")
                return self.sell_reserved_order(
                    ticker,
                    limit_price,
                    use_moo=False,
                    exchange=exchange,
                    holding_quantity=holding_quantity,
                )
            elif use_moo:
                logger.info(f"[{ticker}] Market is closed - placing reserved MOO sell order")
                return self.sell_reserved_order(
                    ticker,
                    limit_price=None,
                    use_moo=True,
                    exchange=exchange,
                    holding_quantity=holding_quantity,
                )
            else:
                logger.warning(f"[{ticker}] Market is closed and no limit_price/use_moo provided")
                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': 0,
                    'message': 'US market is closed. Provide limit_price or use_moo=True for reserved order.'
                }

    async def _get_stock_lock(self, ticker: str) -> asyncio.Lock:
        """Get per-stock lock (prevent concurrent trades on same stock)"""
        if ticker not in self._stock_locks:
            self._stock_locks[ticker] = asyncio.Lock()
        return self._stock_locks[ticker]

    async def async_buy_stock(self, ticker: str, buy_amount: Optional[float] = None,
                              exchange: str = None, timeout: float = 30.0,
                              limit_price: Optional[float] = None) -> Dict[str, Any]:
        """
        Async buy API with timeout

        Args:
            ticker: Stock ticker symbol
            buy_amount: Buy amount in USD
            exchange: Exchange code
            timeout: Timeout in seconds
            limit_price: Limit price for reserved order when market is closed

        Returns:
            Order result dict
        """
        try:
            return await asyncio.wait_for(
                self._execute_buy_stock(ticker, buy_amount, exchange, limit_price),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return {
                'success': False,
                'ticker': ticker,
                'current_price': 0,
                'quantity': 0,
                'total_amount': 0,
                'order_no': None,
                'message': f'Buy request timeout ({timeout}s)',
                'timestamp': datetime.datetime.now().isoformat()
            }

    async def _execute_buy_stock(self, ticker: str, buy_amount: float = None,
                                 exchange: str = None, limit_price: float = None) -> Dict[str, Any]:
        """Execute buy stock logic"""
        amount = self._resolve_buy_amount(buy_amount)

        result = {
            'success': False,
            'ticker': ticker,
            'current_price': 0,
            'quantity': 0,
            'total_amount': 0,
            'order_no': None,
            'message': '',
            'timestamp': datetime.datetime.now().isoformat()
        }

        stock_lock = await self._get_stock_lock(ticker)

        async with stock_lock:
            async with self._semaphore:
                async with self._global_lock:
                    try:
                        logger.info(f"[Async Buy] {ticker} starting (amount: ${amount:.2f})")

                        # Get current price
                        price_info = await asyncio.to_thread(
                            self.get_current_price, ticker, exchange
                        )
                        await asyncio.sleep(0.5)

                        if not price_info:
                            if limit_price and limit_price > 0:
                                # KIS API returned empty price (market closed / pre-market)
                                # Use caller-provided limit_price as fallback for reserved order
                                logger.info(f"[{ticker}] KIS price unavailable, using limit_price ${limit_price:.2f} for reserved order")
                                price_info = {
                                    'ticker': ticker.upper(),
                                    'stock_name': '',
                                    'current_price': limit_price,
                                    'change_rate': 0.0,
                                    'volume': 0,
                                    'exchange': exchange or ''
                                }
                            else:
                                result['message'] = 'Failed to get current price'
                                return result

                        result['current_price'] = price_info['current_price']

                        # Calculate buy quantity using the same KIS orderable amount
                        # resolver as the synchronous buy path. This keeps
                        # async balance_split buys from rejecting small USD cash
                        # balances before opt-in auto exchange can be considered.
                        current_price = price_info['current_price']
                        resolved_amount, buy_info = await asyncio.to_thread(
                            self._resolve_orderable_usd,
                            ticker,
                            amount,
                            current_price,
                            EXCHANGE_CODES.get(exchange.upper(), exchange) if exchange else get_exchange_code(ticker),
                        )
                        buy_quantity = math.floor(resolved_amount / current_price)

                        if buy_quantity == 0:
                            result['message'] = f'Buy quantity is 0 (amount: ${resolved_amount:.2f})'
                            result.update(buy_info)
                            result['resolved_amount'] = resolved_amount
                            return result

                        result.update(buy_info)
                        result['resolved_amount'] = resolved_amount
                        result['quantity'] = buy_quantity
                        result['total_amount'] = buy_quantity * current_price

                        # Execute buy
                        await asyncio.sleep(0.5)

                        # Use current_price as limit_price if not provided or invalid
                        # This is important for reserved orders when market is closed
                        effective_limit_price = limit_price if (limit_price and limit_price > 0) else current_price
                        logger.info(f"[Async Buy] {ticker} limit_price: ${effective_limit_price:.2f} (provided: {limit_price})")

                        buy_result = await asyncio.to_thread(
                            self.smart_buy, ticker, resolved_amount, exchange, effective_limit_price
                        )

                        if buy_result['success']:
                            result['quantity'] = int(buy_result.get('quantity') or buy_quantity)
                            result['total_amount'] = result['quantity'] * effective_limit_price
                            result['success'] = True
                            result['order_no'] = buy_result['order_no']
                            result['message'] = f"Buy completed: {result['quantity']} shares x ${effective_limit_price:.2f} = ${result['total_amount']:.2f}"
                        else:
                            result['message'] = f"Buy failed: {buy_result['message']}"

                    except Exception as e:
                        result['message'] = f'Async buy error: {str(e)}'
                        logger.error(f"[Async Buy] {ticker} error: {str(e)}")

                    await asyncio.sleep(0.1)

        return result

    async def async_sell_stock(self, ticker: str, exchange: str = None,
                               timeout: float = 30.0, limit_price: Optional[float] = None,
                               use_moo: bool = False,
                               sell_fraction: Optional[float] = None) -> Dict[str, Any]:
        """
        Async sell API with timeout

        Args:
            ticker: Stock ticker symbol
            exchange: Exchange code
            timeout: Timeout in seconds
            limit_price: Limit price for reserved order when market is closed
            use_moo: Use Market On Open for reserved order

        Returns:
            Order result dict
        """
        try:
            return await asyncio.wait_for(
                self._execute_sell_stock(ticker, exchange, limit_price, use_moo, sell_fraction),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return {
                'success': False,
                'ticker': ticker,
                'current_price': 0,
                'quantity': 0,
                'estimated_amount': 0,
                'order_no': None,
                'message': f'Sell request timeout ({timeout}s)',
                'timestamp': datetime.datetime.now().isoformat()
            }

    async def _execute_sell_stock(self, ticker: str, exchange: str = None,
                                  limit_price: float = None, use_moo: bool = False,
                                  sell_fraction: Optional[float] = None) -> Dict[str, Any]:
        """Execute sell stock logic with portfolio verification"""
        result = {
            'success': False,
            'ticker': ticker,
            'current_price': 0,
            'quantity': 0,
            'estimated_amount': 0,
            'order_no': None,
            'message': '',
            'timestamp': datetime.datetime.now().isoformat()
        }

        stock_lock = await self._get_stock_lock(ticker)

        async with stock_lock:
            async with self._semaphore:
                async with self._global_lock:
                    try:
                        logger.info(f"[Async Sell] {ticker} starting")

                        # Verify portfolio holdings
                        portfolio = await asyncio.to_thread(self.get_portfolio)

                        target_stock = None
                        for stock in portfolio:
                            if stock['ticker'].upper() == ticker.upper():
                                target_stock = stock
                                break

                        if not target_stock:
                            result['message'] = f'{ticker} not found in portfolio'
                            return result

                        if target_stock['quantity'] <= 0:
                            result['message'] = f'{ticker} quantity is 0'
                            return result

                        holding_quantity = target_stock['quantity']
                        if sell_fraction is not None:
                            if not 0 < sell_fraction <= 1:
                                result['message'] = 'sell_fraction must be greater than 0 and at most 1'
                                return result
                            holding_quantity = math.floor(holding_quantity * sell_fraction)
                            if holding_quantity <= 0:
                                result['message'] = 'Partial sell quantity rounds down to 0 shares'
                                return result

                        resolved_exchange = exchange or target_stock.get('exchange')

                        logger.info(f"[Async Sell] {ticker} holdings verified: {target_stock['quantity']} shares")

                        # Get current price for estimate
                        price_info = await asyncio.to_thread(
                            self.get_current_price, ticker, resolved_exchange
                        )

                        current_price = 0.0
                        if price_info:
                            current_price = price_info['current_price']
                            result['current_price'] = current_price

                        # Use current_price as limit_price if not provided or invalid
                        # This is important for reserved orders when market is closed
                        effective_limit_price = limit_price if (limit_price and limit_price > 0) else current_price

                        # If no valid price at all, use MOO (Market On Open) for reserved orders
                        effective_use_moo = use_moo
                        if effective_limit_price <= 0 and not use_moo:
                            logger.warning(f"[Async Sell] {ticker} no valid limit_price, using MOO")
                            effective_use_moo = True

                        logger.info(f"[Async Sell] {ticker} limit_price: ${effective_limit_price:.2f}, use_moo: {effective_use_moo}")

                        # Execute sell
                        sell_result = await asyncio.to_thread(
                            self.smart_sell_all,
                            ticker,
                            resolved_exchange,
                            effective_limit_price if effective_limit_price > 0 else None,
                            effective_use_moo,
                            holding_quantity,
                        )

                        if sell_result['success']:
                            result['success'] = True
                            result['quantity'] = sell_result['quantity']
                            result['order_no'] = sell_result['order_no']

                            if result['current_price'] > 0:
                                result['estimated_amount'] = result['quantity'] * result['current_price']

                            result['avg_price'] = target_stock.get('avg_price', 0)
                            result['profit_amount'] = target_stock.get('profit_amount', 0)
                            result['profit_rate'] = target_stock.get('profit_rate', 0)

                            result['message'] = (f"Sell completed: {result['quantity']} shares "
                                               f"(avg: ${result['avg_price']:.2f}, "
                                               f"est: ${result['estimated_amount']:.2f}, "
                                               f"P/L: {result['profit_rate']:+.2f}%)")
                        else:
                            result['message'] = f"Sell failed: {sell_result['message']}"

                    except Exception as e:
                        result['message'] = f'Async sell error: {str(e)}'
                        logger.error(f"[Async Sell] {ticker} error: {str(e)}")

                    await asyncio.sleep(0.1)

        return result

    def get_portfolio(self) -> List[Dict[str, Any]]:
        """
        Get current US stock portfolio

        Returns:
            [{
                'ticker': 'AAPL',
                'stock_name': 'APPLE INC',
                'quantity': 10,
                'avg_price': 150.00,
                'current_price': 185.50,
                'eval_amount': 1855.00,
                'profit_amount': 355.00,
                'profit_rate': 23.67,
                'exchange': 'NASD'
            }, ...]
        """
        api_url = "/uapi/overseas-stock/v1/trading/inquire-balance"

        if self.mode == "real":
            tr_id = "TTTS3012R"  # Real overseas balance
        else:
            tr_id = "VTTS3012R"  # Demo overseas balance

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": "NASD",  # Default to NASDAQ, loop through others
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": ""
        }

        portfolio = []

        # Query each exchange
        for exchange in ["NASD", "NYSE", "AMEX"]:
            params["OVRS_EXCG_CD"] = exchange

            try:
                res = self._request(api_url, tr_id, params)

                if res.isOK():
                    output1 = res.getBody().output1

                    if not isinstance(output1, list):
                        output1 = [output1] if output1 else []

                    for item in output1:
                        # Use safe conversion to handle empty strings
                        quantity = _safe_int(item.get('ovrs_cblc_qty'))
                        if quantity > 0:
                            stock_info = {
                                'ticker': item.get('ovrs_pdno', ''),
                                'stock_name': item.get('ovrs_item_name', ''),
                                'quantity': quantity,
                                'avg_price': _safe_float(item.get('pchs_avg_pric')),
                                'current_price': _safe_float(item.get('now_pric2')),
                                'eval_amount': _safe_float(item.get('ovrs_stck_evlu_amt')),
                                'profit_amount': _safe_float(item.get('frcr_evlu_pfls_amt')),
                                'profit_rate': _safe_float(item.get('evlu_pfls_rt')),
                                'exchange': exchange
                            }
                            portfolio.append(stock_info)

                time.sleep(0.1)  # Rate limit

            except Exception as e:
                logger.error(f"Error getting portfolio for {exchange}: {str(e)}")
                continue

        # Deduplicate by ticker (KIS API may return same stock from multiple exchanges)
        seen_tickers = set()
        unique_portfolio = []
        for stock in portfolio:
            ticker = stock.get('ticker')
            if ticker and ticker not in seen_tickers:
                seen_tickers.add(ticker)
                unique_portfolio.append(stock)

        logger.info(f"Portfolio: {len(unique_portfolio)} US stocks held")
        return unique_portfolio

    def get_account_summary(self) -> Optional[Dict[str, Any]]:
        """
        Get account summary for US stocks including USD cash balance

        Returns:
            {
                'total_eval_amount': Total stock evaluation in USD,
                'total_profit_amount': Total P/L in USD,
                'total_profit_rate': Total P/L rate (%),
                'available_amount': Available USD for trading,
                'usd_cash': USD cash balance,
                'exchange_rate': USD/KRW exchange rate
            }
        """
        # Use inquire-present-balance API for accurate USD cash info
        api_url = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
        tr_id = "CTRP6504R"  # Overseas stock settlement-based current balance

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "WCRC_FRCR_DVSN_CD": "02",  # 02: Foreign currency
            "NATN_CD": "840",  # USA
            "TR_MKET_CD": "00",  # All
            "INQR_DVSN_CD": "00"  # All
        }

        try:
            res = self._request(api_url, tr_id, params)

            if res.isOK():
                body = res.getBody()
                output2 = body.output2 if hasattr(body, 'output2') else []
                output3 = body.output3 if hasattr(body, 'output3') else {}

                # Extract USD info from output2
                usd_cash = 0.0
                exchange_rate = 0.0

                if output2 and isinstance(output2, list):
                    for item in output2:
                        if item.get('crcy_cd') == 'USD':
                            usd_cash = _safe_float(item.get('frcr_dncl_amt_2'))
                            exchange_rate = _safe_float(item.get('frst_bltn_exrt'))
                            break

                # Calculate from portfolio for stock totals
                portfolio = self.get_portfolio()
                total_eval = sum(s['eval_amount'] for s in portfolio)
                total_profit = sum(s['profit_amount'] for s in portfolio)
                total_cost = sum(s['avg_price'] * s['quantity'] for s in portfolio)

                summary = {
                    'total_eval_amount': total_eval,
                    'total_profit_amount': total_profit,
                    'total_profit_rate': (total_profit / total_cost * 100) if total_cost > 0 else 0,
                    'available_amount': usd_cash,  # USD cash available for trading
                    'usd_cash': usd_cash,
                    'exchange_rate': exchange_rate,
                }

                logger.info(f"Account Summary: Stock Eval ${summary['total_eval_amount']:.2f}, "
                           f"P/L ${summary['total_profit_amount']:+.2f} "
                           f"({summary['total_profit_rate']:+.2f}%), "
                           f"USD Cash ${summary['usd_cash']:.2f}")

                return summary

            logger.error(f"Account summary API failed: {res.getErrorCode()} - {res.getErrorMessage()}")
            return None

        except Exception as e:
            logger.error(f"Error getting account summary: {str(e)}")
            return None


class MultiAccountUSStockTrading:
    """Fan out trading orders to all configured US accounts for the current mode."""

    def __init__(self, mode: str, buy_amount: float = None, auto_trading: bool = USStockTrading.AUTO_TRADING, product_code: str = "01"):
        from .modes import normalize_trading_mode

        self.mode = normalize_trading_mode(mode)
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading
        self.product_code = str(product_code)

        svr = "vps" if self.mode == "demo" else "prod"
        self.account_configs = ka.get_configured_accounts(svr=svr, product=self.product_code, market="us")
        self._traders: dict[str, USStockTrading] = {}
        self.primary_account = None
        try:
            self.primary_account = ka.resolve_account(svr=svr, product=self.product_code, market="us")
        except ValueError:
            logger.warning("No US accounts configured for multi-account trading")

    def _get_trader(self, account: Dict[str, Any]) -> USStockTrading:
        trader = self._traders.get(account["account_key"])
        if trader is None:
            trader = USStockTrading(
                mode=self.mode,
                buy_amount=self.buy_amount,
                auto_trading=self.auto_trading,
                account_name=account["name"],
                product_code=account["product"],
            )
            self._traders[account["account_key"]] = trader
        return trader

    def _get_primary_trader(self) -> USStockTrading:
        if not self.primary_account:
            raise RuntimeError("No primary US account configured")
        return self._get_trader(self.primary_account)

    async def async_buy_stock(self, ticker: str, buy_amount: Optional[float] = None,
                              exchange: str = None, timeout: float = 30.0, limit_price: Optional[float] = None) -> Dict[str, Any]:
        if not self.account_configs:
            return self._aggregate_results(ticker, [], action="buy")
        results = []
        for account in self.account_configs:
            trader = self._get_trader(account)
            result = await trader.async_buy_stock(
                ticker=ticker,
                buy_amount=buy_amount,
                exchange=exchange,
                timeout=timeout,
                limit_price=limit_price,
            )
            result["account_name"] = account["name"]
            result["account_key"] = account["account_key"]
            results.append(result)

        return self._aggregate_results(ticker, results, action="buy")

    async def async_sell_stock(self, ticker: str, exchange: str = None,
                               timeout: float = 30.0, limit_price: Optional[float] = None,
                               use_moo: bool = False,
                               sell_fraction: Optional[float] = None) -> Dict[str, Any]:
        if not self.account_configs:
            return self._aggregate_results(ticker, [], action="sell")
        results = []
        for account in self.account_configs:
            trader = self._get_trader(account)
            sell_kwargs = {}
            if sell_fraction is not None:
                sell_kwargs["sell_fraction"] = sell_fraction
            result = await trader.async_sell_stock(
                ticker=ticker,
                exchange=exchange,
                timeout=timeout,
                limit_price=limit_price,
                use_moo=use_moo,
                **sell_kwargs,
            )
            result["account_name"] = account["name"]
            result["account_key"] = account["account_key"]
            results.append(result)

        return self._aggregate_results(ticker, results, action="sell")

    def get_portfolio(self) -> List[Dict[str, Any]]:
        return self._get_primary_trader().get_portfolio()

    def get_account_summary(self) -> Optional[Dict[str, Any]]:
        return self._get_primary_trader().get_account_summary()

    def get_current_price(self, ticker: str, exchange: str = None) -> Optional[Dict[str, Any]]:
        return self._get_primary_trader().get_current_price(ticker, exchange)

    def calculate_buy_quantity(self, ticker: str, buy_amount: float = None, exchange: str = None) -> int:
        return self._get_primary_trader().calculate_buy_quantity(ticker, buy_amount, exchange)

    def get_holding_quantity(self, ticker: str) -> int:
        return self._get_primary_trader().get_holding_quantity(ticker)

    def _aggregate_results(self, ticker: str, results: List[Dict[str, Any]], action: str) -> Dict[str, Any]:
        success_count = sum(1 for result in results if result.get("success"))
        total_accounts = len(results)
        total_quantity = sum(result.get("quantity", 0) for result in results)
        total_amount = sum(result.get("total_amount", result.get("estimated_amount", 0)) for result in results)
        successful_accounts = [result.get("account_name") for result in results if result.get("success")]
        failed_accounts = [result.get("account_name") for result in results if not result.get("success")]

        messages = [
            f"{result.get('account_name')}: {result.get('message', '')}"
            for result in results
        ]

        if total_accounts == 0:
            return {
                "success": False,
                "partial_success": False,
                "ticker": ticker,
                "quantity": 0,
                "total_amount": 0,
                "estimated_amount": 0,
                "order_no": None,
                "message": f"No US accounts configured for {action}",
                "account_results": [],
                "successful_accounts": [],
                "failed_accounts": [],
            }

        return {
            "success": success_count == total_accounts and total_accounts > 0,
            "partial_success": 0 < success_count < total_accounts,
            "ticker": ticker,
            "quantity": total_quantity,
            "total_amount": total_amount,
            "estimated_amount": total_amount,
            "order_no": None,
            "message": f"{action} executed for {success_count}/{total_accounts} accounts | " + " ; ".join(messages),
            "account_results": results,
            "successful_accounts": successful_accounts,
            "failed_accounts": failed_accounts,
        }


class MultiAccountUSTradingContext:
    """Explicit multi-account US trading context."""

    def __init__(
        self,
        mode: str = USStockTrading.DEFAULT_MODE,
        buy_amount: float = None,
        auto_trading: bool = USStockTrading.AUTO_TRADING,
        product_code: str = "01",
    ):
        self.mode = mode
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading
        self.product_code = product_code
        self.trader = None

    async def __aenter__(self):
        self.trader = MultiAccountUSStockTrading(
            mode=self.mode,
            buy_amount=self.buy_amount,
            auto_trading=self.auto_trading,
            product_code=self.product_code,
        )
        return self.trader

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            logger.error(f"MultiAccountUSTradingContext error: {exc_type.__name__}: {exc_val}")


# Context Manager
class AsyncUSTradingContext:
    """Async trading context manager for safe resource management"""

    DEFAULT_BUY_AMOUNT = _cfg.get("default_unit_amount_usd", 100)
    AUTO_TRADING = _cfg.get("auto_trading", True)
    DEFAULT_MODE = _cfg.get("default_mode", "demo")

    def __init__(
        self,
        mode: str = None,
        buy_amount: float = None,
        auto_trading: bool = None,
        account_name: str = None,
        account_index: int = None,
        product_code: str = "01",
    ):
        self.mode = mode if mode else self.DEFAULT_MODE
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading if auto_trading is not None else self.AUTO_TRADING
        self.account_name = account_name
        self.account_index = account_index
        self.product_code = product_code
        self.trader = None

    async def __aenter__(self):
        self.trader = USStockTrading(
            mode=self.mode,
            buy_amount=self.buy_amount,
            auto_trading=self.auto_trading,
            account_name=self.account_name,
            account_index=self.account_index,
            product_code=self.product_code,
        )
        return self.trader

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            logger.error(f"AsyncUSTradingContext error: {exc_type.__name__}: {exc_val}")
