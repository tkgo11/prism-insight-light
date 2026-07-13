# -*- coding: utf-8 -*-
# ====|  Sample API calls for obtaining (REST) access token / (Websocket) connection key  |=====================
# ====|  Includes common API call functions                                  |=====================

from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import os
import shutil
import tempfile
import time
import threading
import warnings
from base64 import b64decode
from collections import namedtuple
from collections.abc import Callable
from datetime import datetime
from zoneinfo import ZoneInfo
from io import StringIO
import stat
import hashlib
import importlib
import importlib.util
from pathlib import Path
from typing import Any, Optional, Tuple

from .buy_sizing import normalize_amount, normalize_percent
from .config_paths import active_kis_config_path

pd_spec = importlib.util.find_spec("pandas")
if pd_spec is not None:
    pd = importlib.import_module("pandas")
else:  # pragma: no cover - exercised only in minimal test environments
    class _PandasFallback:
        class DataFrame:
            pass

        @staticmethod
        def read_csv(*args, **kwargs):
            raise ModuleNotFoundError("pandas is required for KIS websocket tabular data parsing")

    pd = _PandasFallback()

tenacity_spec = importlib.util.find_spec("tenacity")
if tenacity_spec is not None:
    _tenacity = importlib.import_module("tenacity")
    retry = _tenacity.retry
    stop_after_attempt = _tenacity.stop_after_attempt
    wait_exponential = _tenacity.wait_exponential
    retry_if_exception_type = _tenacity.retry_if_exception_type
else:  # pragma: no cover - minimal test environment fallback
    def retry(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

    def stop_after_attempt(*args, **kwargs):
        return None

    def wait_exponential(*args, **kwargs):
        return None

    def retry_if_exception_type(*args, **kwargs):
        return None

# pip install requests (package installation)
requests_spec = importlib.util.find_spec("requests")
if requests_spec is not None:
    requests = importlib.import_module("requests")
else:  # pragma: no cover - minimal test environment fallback
    class _RequestsFallback:
        class RequestException(Exception):
            pass

        @staticmethod
        def get(*args, **kwargs):
            raise _RequestsFallback.RequestException("requests is required for HTTP GET calls")

        @staticmethod
        def post(*args, **kwargs):
            raise _RequestsFallback.RequestException("requests is required for HTTP POST calls")

    requests = _RequestsFallback()

# Declare web socket module
websockets_spec = importlib.util.find_spec("websockets")
if websockets_spec is not None:
    websockets = importlib.import_module("websockets")
else:  # pragma: no cover - minimal test environment fallback
    class _WebSocketsFallback:
        class ClientConnection:
            pass

        @staticmethod
        def connect(*args, **kwargs):
            raise RuntimeError("websockets is required for websocket connections")

    websockets = _WebSocketsFallback()

# pip install PyYAML (package installation)
from . import yaml_compat as yaml
crypto_root_spec = importlib.util.find_spec("Crypto")
crypto_spec = importlib.util.find_spec("Crypto.Cipher") if crypto_root_spec is not None else None
crypto_padding_spec = importlib.util.find_spec("Crypto.Util.Padding") if crypto_root_spec is not None else None
if crypto_spec is not None and crypto_padding_spec is not None:
    AES = importlib.import_module("Crypto.Cipher.AES")
    unpad = importlib.import_module("Crypto.Util.Padding").unpad
else:  # pragma: no cover - minimal test environment fallback
    class _AESFallback:
        MODE_CBC = 1
        block_size = 16

        @staticmethod
        def new(*args, **kwargs):
            raise RuntimeError("pycryptodome is required for encrypted websocket payloads")

    AES = _AESFallback()

    def unpad(data, block_size):
        return data

cryptography_spec = importlib.util.find_spec("cryptography")
fernet_spec = importlib.util.find_spec("cryptography.fernet") if cryptography_spec is not None else None
if fernet_spec is not None:
    Fernet = importlib.import_module("cryptography.fernet").Fernet
else:  # pragma: no cover - minimal test environment fallback
    class Fernet:
        @staticmethod
        def generate_key():
            raise RuntimeError("cryptography is required for encrypted KIS token storage")

        def __init__(self, key):
            raise RuntimeError("cryptography is required for encrypted KIS token storage")

        def encrypt(self, data: bytes) -> bytes:
            raise RuntimeError("cryptography is required for encrypted KIS token storage")

        def decrypt(self, token: bytes) -> bytes:
            raise RuntimeError("cryptography is required for encrypted KIS token storage")

class SecurityError(Exception):
    """Security-related errors"""
    pass


# ============== KIS Authentication Exception Classes ==============
class KISAuthError(Exception):
    """Base exception for KIS authentication errors"""
    pass


class TokenFileError(KISAuthError):
    """Token file read/write errors"""
    pass


class CredentialMismatchError(KISAuthError):
    """Credential and mode mismatch detected (e.g., demo key with real mode)"""
    pass


class TokenRequestError(KISAuthError):
    """Failed to request new token from KIS API"""
    def __init__(self, message, status_code=None, response_text=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


KIS_RATE_LIMIT_ERROR_CODE = "EGW00201"
KIS_EXPIRED_TOKEN_ERROR_CODE = "EGW00123"
KIS_TOKEN_EXPIRY_TZ = ZoneInfo("Asia/Seoul")


def _nonnegative_int_env(name: str, default: int) -> int:
    raw_value = os.environ.get(name, str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a non-negative integer") from exc
    if value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _finite_float_env(name: str, default: float, *, positive: bool) -> float:
    raw_value = os.environ.get(name, str(default))
    try:
        value = float(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number") from exc
    if not math.isfinite(value) or (value <= 0 if positive else value < 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a finite {qualifier} number")
    return value


KIS_RATE_LIMIT_RETRY_ATTEMPTS = _nonnegative_int_env("KIS_RATE_LIMIT_RETRY_ATTEMPTS", 10)
KIS_RATE_LIMIT_RETRY_BASE_SECONDS = _finite_float_env(
    "KIS_RATE_LIMIT_RETRY_BASE_SECONDS", 1.0, positive=False
)
KIS_RATE_LIMIT_RETRY_MAX_SECONDS = _finite_float_env(
    "KIS_RATE_LIMIT_RETRY_MAX_SECONDS", 5.0, positive=False
)
KIS_HTTP_CONNECT_TIMEOUT_SECONDS = _finite_float_env(
    "KIS_HTTP_CONNECT_TIMEOUT_SECONDS", 5.0, positive=True
)
KIS_HTTP_READ_TIMEOUT_SECONDS = _finite_float_env(
    "KIS_HTTP_READ_TIMEOUT_SECONDS", 30.0, positive=True
)
KIS_HTTP_TIMEOUT = (KIS_HTTP_CONNECT_TIMEOUT_SECONDS, KIS_HTTP_READ_TIMEOUT_SECONDS)


clearConsole = lambda: os.system("cls" if os.name in ("nt", "dos") else "clear")

key_bytes = 32
# Find config folder based on kis_auth.py file directory
current_dir = os.path.dirname(os.path.abspath(__file__))
config_root = os.path.join(current_dir, "config")
# config_root = "$HOME/KIS/config/"  # Folder where token files are stored, set path to be difficult for third parties to find
# token_tmp = config_root + 'KIS000000'  # Specify file name for local token storage, avoid file names that can infer token value
# token_tmp = config_root + 'KIS' + datetime.today().strftime("%Y%m%d%H%M%S")  # Token local storage filename YYYYMMDDHHMMSS
# Create directory if it doesn't exist
os.makedirs(config_root, exist_ok=True)

# Set directory permissions on Unix/Linux (owner only access)
if os.name != 'nt':
    try:
        os.chmod(config_root, stat.S_IRWXU)  # 700 permission
    except:
        pass  # Ignore permission change failure

# Generate security-enhanced token filename
def get_token_filename():
    """Generate unpredictable token filename"""
    # Default: Date-based (compatible with existing method)
    date_str = datetime.today().strftime('%Y%m%d')

    # Security enhancement: Add random suffix (optional)
    # Control security level via environment variable
    if os.environ.get('KIS_SECURE_TOKEN', 'false').lower() == 'true':
        random_suffix = hashlib.md5(os.urandom(8)).hexdigest()[:8]
        return os.path.join(config_root, f"KIS_{date_str}_{random_suffix}.token")
    else:
        # Maintain existing method (compatibility)
        return os.path.join(config_root, f"KIS{date_str}")

token_tmp = get_token_filename()

# NOTE: Removed empty file creation at module import time (Bug #1)
# Empty token files cause authentication failures and should only be created
# when saving a valid token via save_token()

# Store and manage app key, app secret, token, account number, etc., set to your own path and filename.
# pip install PyYAML (package installation)
config_file = str(active_kis_config_path())
with open(config_file, encoding="UTF-8") as f:
    _cfg = yaml.safe_load(f)


DEFAULT_PRODUCT_CODE = str(_cfg.get("default_product_code", "01"))
DEFAULT_BUY_AMOUNT_KRW = int(_cfg.get("default_unit_amount", 0) or 0)
DEFAULT_BUY_AMOUNT_USD = float(_cfg.get("default_unit_amount_usd", 0) or 0)
DEFAULT_BUY_PERCENT_KRW = normalize_percent(_cfg.get("default_unit_asset_percent"))
DEFAULT_BUY_PERCENT_USD = normalize_percent(_cfg.get("default_unit_asset_percent_usd"))
MAX_CONFIGURED_ACCOUNTS = 10


def mask_account_number(account_number: str | None) -> str:
    """Mask an account number for safe logging."""
    if not account_number:
        return ""
    account_str = str(account_number)
    if len(account_str) <= 4:
        return "*" * len(account_str)
    if len(account_str) <= 6:
        return f"{account_str[:2]}{'*' * (len(account_str) - 4)}{account_str[-2:]}"
    return f"{account_str[:2]}{'*' * (len(account_str) - 4)}{account_str[-2:]}"


def _normalize_market(market: str | None) -> str | None:
    if market is None:
        return None
    normalized = str(market).strip().lower()
    aliases = {
        "kr": "kr",
        "korea": "kr",
        "domestic": "kr",
        "kor": "kr",
        "us": "us",
        "usa": "us",
        "america": "us",
        "overseas": "us",
        "all": "all",
        "both": "all",
    }
    if normalized not in aliases:
        raise ValueError(f"Unknown market '{market}'. Expected one of: kr, us, all.")
    return aliases[normalized]


def _normalize_server_mode(mode: str | None) -> str | None:
    """Normalize mode aliases to KIS server identifiers."""
    if mode is None:
        return None

    normalized = str(mode).strip().lower()
    if normalized in {"real", "prod", "live"}:
        return "prod"
    if normalized in {"demo", "paper", "vps", "mock"}:
        return "vps"
    raise ValueError(f"Unknown server mode '{mode}'. Expected one of: real/prod/live or demo/paper/vps/mock.")


def _to_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _normalize_buy_amount(value: Any) -> float | None:
    return normalize_amount(value)


def _normalize_buy_percent(value: Any) -> float | None:
    return normalize_percent(value)


def _build_normalized_account(
    index: int,
    item: dict[str, Any],
    *,
    primary_default: bool = False,
) -> dict[str, Any] | None:
    if not isinstance(item, dict):
        return None
    if item.get("enabled", True) is False:
        return None

    account_number = item.get("account") or item.get("account_number") or item.get("cano")
    if not account_number:
        return None

    account_mode = _normalize_server_mode(item.get("mode") or item.get("svr") or item.get("environment"))
    product = str(item.get("product") or item.get("product_code") or DEFAULT_PRODUCT_CODE)
    market = _normalize_market(item.get("market") or "all") or "all"
    account_name = str(item.get("name") or item.get("id") or f"account-{index + 1}")

    normalized = {
        "name": account_name,
        "svr": account_mode,
        "product": product,
        "account": str(account_number),
        "market": market,
        "primary": _to_bool(item.get("primary"), default=primary_default),
        "buy_amount_krw": _normalize_buy_amount(item.get("buy_amount_krw") or item.get("buy_amount")),
        "buy_amount_usd": _normalize_buy_amount(item.get("buy_amount_usd")),
        "buy_percent_krw": _normalize_buy_percent(
            item.get("buy_percent_krw")
            if item.get("buy_percent_krw") is not None
            else item.get("buy_asset_percent_krw", DEFAULT_BUY_PERCENT_KRW)
        ),
        "buy_percent_usd": _normalize_buy_percent(
            item.get("buy_percent_usd")
            if item.get("buy_percent_usd") is not None
            else item.get("buy_asset_percent_usd", DEFAULT_BUY_PERCENT_USD)
        ),
        # Per-account API credentials (optional; fall back to global my_app/my_sec if absent)
        "app_key": item.get("app_key") or item.get("appkey") or None,
        "app_secret": item.get("app_secret") or item.get("appsecret") or None,
    }
    normalized["account_key"] = f"{normalized['svr']}:{normalized['account']}:{normalized['product']}"
    return normalized


def _build_legacy_accounts() -> list[dict[str, Any]]:
    """Build normalized accounts from legacy config keys."""
    legacy_accounts: list[dict[str, Any]] = []
    legacy_candidates = [
        ("my_acct_stock", "prod", "legacy-real-stock", True, "all"),
        ("my_paper_stock", "vps", "legacy-demo-stock", True, "all"),
        ("my_acct_future", "prod", "legacy-real-future", False, "kr"),
        ("my_paper_future", "vps", "legacy-demo-future", False, "kr"),
    ]

    for key, svr, name, primary, market in legacy_candidates:
        account_number = _cfg.get(key)
        if not account_number:
            continue
        # Skip placeholder values (must be a numeric 8-digit account number)
        if not str(account_number).strip().isdigit():
            continue
        legacy_accounts.append(
            _build_normalized_account(
                len(legacy_accounts),
                {
                    "name": name,
                    "mode": svr,
                    "account": str(account_number),
                    "product": str(_cfg.get("my_prod", DEFAULT_PRODUCT_CODE)),
                    "market": market,
                    "primary": primary,
                    "buy_amount_krw": DEFAULT_BUY_AMOUNT_KRW,
                    "buy_amount_usd": DEFAULT_BUY_AMOUNT_USD,
                    "buy_percent_krw": DEFAULT_BUY_PERCENT_KRW,
                    "buy_percent_usd": DEFAULT_BUY_PERCENT_USD,
                },
                primary_default=primary,
            )
        )

    if legacy_accounts:
        warnings.warn(
            "Legacy KIS account config is deprecated. Migrate to the 'accounts' list format in kis_devlp.yaml.",
            DeprecationWarning,
            stacklevel=2,
        )
    return [account for account in legacy_accounts if account]


def get_configured_accounts(
    svr: str | None = None,
    product: str | None = None,
    market: str | None = None,
    primary_only: bool = False,
) -> list[dict]:
    """
    Return normalized account definitions from config.

    Requires the multi-account `accounts` list in kis_devlp.yaml.
    """
    requested_svr = _normalize_server_mode(svr) if svr is not None else None
    requested_product = str(product) if product is not None else None
    requested_market = _normalize_market(market) if market else None

    raw_accounts = _cfg.get("accounts")
    normalized_accounts: list[dict] = []

    if isinstance(raw_accounts, list):
        for index, item in enumerate(raw_accounts):
            normalized = _build_normalized_account(index, item)
            if normalized:
                normalized_accounts.append(normalized)

    if not normalized_accounts:
        normalized_accounts = _build_legacy_accounts()

    if not normalized_accounts:
        raise ValueError(
            "No accounts configured. Define at least one entry in 'accounts' inside kis_devlp.yaml "
            "or provide legacy account fields."
        )

    if len(normalized_accounts) > MAX_CONFIGURED_ACCOUNTS:
        raise ValueError(
            f"Too many accounts configured ({len(normalized_accounts)}). Maximum supported accounts: {MAX_CONFIGURED_ACCOUNTS}."
        )

    filtered_accounts = []
    for account in normalized_accounts:
        if requested_svr and account["svr"] != requested_svr:
            continue
        if requested_product and account["product"] != requested_product:
            continue
        if requested_market and account["market"] not in {requested_market, "all", "both"}:
            continue
        if primary_only and not account.get("primary", False):
            continue
        filtered_accounts.append(account)

    if primary_only and not filtered_accounts and normalized_accounts:
        filtered_accounts = [
            account for account in normalized_accounts
            if (not requested_svr or account["svr"] == requested_svr)
            and (not requested_product or account["product"] == requested_product)
            and (not requested_market or account["market"] in {requested_market, "all", "both"})
        ]
        filtered_accounts = filtered_accounts[:1]

    return filtered_accounts


def resolve_account(
    svr: str,
    product: str | None = None,
    account_name: str | None = None,
    account_index: int | None = None,
    market: str | None = None,
    account_key: str | None = None,
) -> dict:
    """Resolve a single account from config for the requested server/product."""
    requested_svr = _normalize_server_mode(svr)
    requested_product = str(product) if product is not None else None
    accounts = get_configured_accounts(svr=requested_svr, market=market)

    if account_key:
        matched = [account for account in accounts if account["account_key"] == account_key]
        if not matched:
            raise ValueError(f"Account key '{account_key}' not found for mode '{requested_svr}'")
        if requested_product and matched[0]["product"] != requested_product:
            raise ValueError(
                f"Account '{account_key}' does not support product '{requested_product or DEFAULT_PRODUCT_CODE}'"
            )
        return matched[0]

    if account_name:
        matched = [account for account in accounts if account["name"] == account_name]
        if not matched:
            raise ValueError(f"Account '{account_name}' not found for mode '{requested_svr}'")
        if requested_product:
            matched = [account for account in matched if account["product"] == requested_product]
        if not matched:
            raise ValueError(
                f"Account '{account_name}' does not support product '{requested_product or DEFAULT_PRODUCT_CODE}'"
            )
        return matched[0]

    if requested_product:
        product_accounts = [account for account in accounts if account["product"] == requested_product]
        if product_accounts:
            accounts = product_accounts

    if account_index is not None:
        if account_index < 0 or account_index >= len(accounts):
            raise ValueError(
                f"Account index {account_index} is out of range for mode '{requested_svr}' ({len(accounts)} accounts)"
            )
        return accounts[account_index]

    if not accounts:
        raise ValueError(
            f"No accounts configured for mode '{requested_svr}'"
            + (f" and product '{requested_product}'" if requested_product else "")
        )

    for account in accounts:
        if account.get("primary"):
            return account

    return accounts[0]

_TRENV = None
_TRENV_LOCK = threading.RLock()
_last_auth_time = datetime.now()
_autoReAuth = False
_DEBUG = False
_isPaper = False
_smartSleep = 0.1
_CURRENT_AUTH_CONTEXT: dict[str, Any] = {}


def _mask_account_key(account_key: str | None) -> str:
    """Mask account key values that include brokerage account numbers."""
    if not account_key:
        return ""
    parts = str(account_key).split(":")
    if len(parts) >= 2:
        parts[1] = mask_account_number(parts[1])
        return ":".join(parts)
    return mask_account_number(str(account_key))


# Define default header values
_base_headers = {
    "Content-Type": "application/json",
    "Accept": "text/plain",
    "charset": "UTF-8",
    "User-Agent": _cfg["my_agent"],
}

def _get_or_create_encryption_key():
    """Generate or load encryption key"""
    key_file = os.path.join(config_root, ".token_key")

    if os.path.exists(key_file):
        with open(key_file, 'rb') as f:
            key = f.read()
    else:
        # Generate new encryption key
        key = Fernet.generate_key()
        with open(key_file, 'wb') as f:
            f.write(key)

        # Set key file permissions (maximum security)
        if os.name != 'nt':
            os.chmod(key_file, 0o600)
        else:
            # Windows: Set file hidden attribute
            try:
                import ctypes
                ctypes.windll.kernel32.SetFileAttributesW(key_file, 2)  # FILE_ATTRIBUTE_HIDDEN
            except:
                pass

    return key

# Obtain and save token (token value, token validity time 1 day, same token value if applied within 6 hours, notification sent when issued)
def save_token(my_token: str, my_expired: str, account_key: Optional[str] = None):
    """
    Save token securely with encryption and atomic write.

    Improvements:
    - Validates token data before saving (prevents empty token files)
    - Uses atomic write (temp file + rename) to prevent corruption
    - File locking prevents race conditions in multi-process scenarios
    - Cross-platform compatible (Windows and Unix)
    - Per-account token file isolation via account_key

    Args:
        my_token: The access token string
        my_expired: Token expiry datetime string (format: "YYYY-MM-DD HH:MM:SS")
        account_key: Optional account key for per-account token file isolation

    Raises:
        TokenFileError: If token data is invalid or write fails
    """
    # Validate token data BEFORE any file operations
    if not my_token or len(my_token) < 10:
        raise TokenFileError("Cannot save empty or invalid token")

    if not my_expired:
        raise TokenFileError("Cannot save token without expiry date")

    try:
        valid_date = datetime.strptime(my_expired, "%Y-%m-%d %H:%M:%S")
    except ValueError as e:
        raise TokenFileError(f"Invalid expiry date format: {my_expired}") from e

    # Prepare token data
    token_data = {
        "token": my_token,
        "valid_date": valid_date.strftime("%Y-%m-%d %H:%M:%S"),
        "created_at": datetime.now().isoformat(),
        "pid": os.getpid()
    }

    # Encrypt token data
    key = _get_or_create_encryption_key()
    fernet = Fernet(key)
    json_string = json.dumps(token_data, indent=2)
    encrypted_data = fernet.encrypt(json_string.encode('utf-8'))

    # Determine target token file (per-account or global)
    if account_key:
        acct_hash = hashlib.sha256(account_key.encode()).hexdigest()[:8]
        target_token_file = os.path.join(config_root, f"KIS_acct_{acct_hash}.token")
    else:
        target_token_file = token_tmp

    # Atomic write with file locking (prevents race conditions)
    lock_file = os.path.join(config_root, ".token_write.lock")

    try:
        with CrossPlatformFileLock(lock_file, timeout=30):
            # Use atomic write (temp file + rename)
            _atomic_write(target_token_file, encrypted_data)

            # Set secure file permissions
            _set_secure_file_permissions(target_token_file)

            # Clean up old token files
            cleanup_old_tokens()

            logging.info(f"✅ Encrypted token saved atomically: {target_token_file}")

    except TokenFileError:
        raise
    except Exception as e:
        raise TokenFileError(f"Failed to save token: {e}") from e



# Check token (token value, token validity 1 day, same token value if applied within 6 hours, notification sent when issued)
def read_token(account_key: Optional[str] = None) -> Optional[str]:
    """
    Read and validate token with auto-recovery.

    Improvements:
    - Auto-cleanup of empty token files (Issue #137 root cause)
    - Auto-cleanup of corrupted/unreadable token files
    - Auto-cleanup of expired tokens
    - Sorted by modification time (newest first)
    - Cross-platform compatible file deletion
    - Per-account token file isolation via account_key

    Args:
        account_key: Optional account key to look up a per-account token file only

    Returns:
        Valid token string or None if no valid token found
    """
    try:
        if account_key:
            # Per-account mode: only look for the specific account's token file
            acct_token_path = _token_file_for_account(account_key)
            token_files = [acct_token_path] if acct_token_path.exists() else []
        else:
            # Global mode: find all token files (multiple patterns for compatibility)
            token_files = list(Path(config_root).glob("KIS*.token")) + \
                          list(Path(config_root).glob("KIS20*"))

            # Also check current token_tmp path
            if os.path.exists(token_tmp) and Path(token_tmp) not in token_files:
                token_files.append(Path(token_tmp))

        if not token_files:
            logging.debug("No token files found")
            return None

        # Sort by modification time (newest first)
        token_files = sorted(token_files, key=lambda f: f.stat().st_mtime, reverse=True)

        # Try each token file, clean up invalid ones
        for token_file in token_files:
            try:
                file_size = token_file.stat().st_size

                # AUTO-RECOVERY: Delete empty files (Issue #137 fix)
                if file_size == 0:
                    logging.warning(f"⚠️  Found empty token file, deleting: {token_file}")
                    _safe_delete(token_file)
                    continue

                # Try to read and decrypt
                key = _get_or_create_encryption_key()
                fernet = Fernet(key)

                token_data = None
                valid_date_str = None
                token = None

                # Try encrypted format first
                try:
                    with open(token_file, 'rb') as f:
                        encrypted_data = f.read()
                        if not encrypted_data:
                            logging.warning(f"⚠️  Empty token data, deleting: {token_file}")
                            _safe_delete(token_file)
                            continue
                        decrypted_data = fernet.decrypt(encrypted_data)
                        token_data = json.loads(decrypted_data.decode('utf-8'))

                    if token_data and 'valid_date' in token_data and 'token' in token_data:
                        valid_date_str = token_data['valid_date']
                        token = token_data['token']

                except Exception as decrypt_error:
                    # Try legacy JSON format
                    logging.debug(f"Trying legacy format for {token_file}: {decrypt_error}")
                    try:
                        with open(token_file, 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                            if not content:
                                logging.warning(f"⚠️  Empty legacy token, deleting: {token_file}")
                                _safe_delete(token_file)
                                continue
                            token_data = json.loads(content)

                            if token_data and 'valid_date' in token_data and 'token' in token_data:
                                valid_date_str = token_data['valid_date']
                                token = token_data['token']
                    except:
                        # Try YAML format (oldest format)
                        try:
                            with open(token_file, 'r', encoding='UTF-8') as f:
                                content = f.read().strip()
                                if not content:
                                    logging.warning(f"⚠️  Empty YAML token, deleting: {token_file}")
                                    _safe_delete(token_file)
                                    continue
                                tkg_tmp = yaml.safe_load(content)

                                if tkg_tmp and 'valid-date' in tkg_tmp and 'token' in tkg_tmp:
                                    valid_date_str = datetime.strftime(tkg_tmp['valid-date'], "%Y-%m-%d %H:%M:%S")
                                    token = tkg_tmp['token']
                        except Exception as yaml_error:
                            # AUTO-RECOVERY: Delete corrupted files
                            logging.warning(f"⚠️  Corrupted token file, deleting: {token_file} ({yaml_error})")
                            _safe_delete(token_file)
                            continue

                # Validate token data
                if not valid_date_str or not token:
                    logging.warning(f"⚠️  Invalid token data (missing fields), deleting: {token_file}")
                    _safe_delete(token_file)
                    continue

                # Check expiry
                try:
                    valid_date = datetime.strptime(valid_date_str, "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    logging.warning(f"⚠️  Invalid date format in token, deleting: {token_file}")
                    _safe_delete(token_file)
                    continue

                if not _is_token_expired(valid_date):
                    logging.info(f"✅ Valid token found (expires: {valid_date})")
                    return token
                else:
                    # AUTO-RECOVERY: Delete expired tokens
                    logging.info(f"⏰ Token expired at {valid_date}, deleting: {token_file}")
                    _safe_delete(token_file)
                    continue

            except Exception as file_error:
                # AUTO-RECOVERY: Delete unreadable files
                logging.warning(f"⚠️  Error reading token file, deleting: {token_file} ({file_error})")
                _safe_delete(token_file)
                continue

        # No valid token found after checking all files
        logging.info("No valid token found after checking all files")
        return None

    except Exception as e:
        logging.error(f"Error in read_token: {e}")
        return None


def _is_token_expired(valid_date: datetime, *, now: datetime | None = None) -> bool:
    """Return whether a KIS token expiry timestamp has passed.

    KIS returns ``access_token_token_expired`` as a timezone-less timestamp in
    Korean local time.  Treating that value as the server's local timezone (for
    example UTC inside a container) can keep an already-expired token alive for
    hours.  Naive expiry values are therefore interpreted as Asia/Seoul time.
    """
    if valid_date.tzinfo is None:
        valid_date = valid_date.replace(tzinfo=KIS_TOKEN_EXPIRY_TZ)

    if now is None:
        now = datetime.now(KIS_TOKEN_EXPIRY_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=KIS_TOKEN_EXPIRY_TZ)

    return valid_date <= now.astimezone(KIS_TOKEN_EXPIRY_TZ)

def _set_secure_file_permissions(file_path):
    """Set secure file permissions on all OS"""
    try:
        if os.name == 'nt':  # Windows
            # Set ACL on Windows so only owner can access
            try:
                import win32security
                import win32api
                import ntsecuritycon as con

                # Get current user SID
                username = win32api.GetUserName()
                domain = win32api.GetComputerName()
                user_sid, domain, type = win32security.LookupAccountName(domain, username)

                # Create new ACL - owner access only
                sd = win32security.GetFileSecurity(file_path, win32security.DACL_SECURITY_INFORMATION)
                dacl = win32security.ACL()
                dacl.AddAccessAllowedAce(win32security.ACL_REVISION,
                                         con.FILE_ALL_ACCESS, user_sid)
                sd.SetSecurityDescriptorDacl(1, dacl, 0)
                win32security.SetFileSecurity(file_path, win32security.DACL_SECURITY_INFORMATION, sd)

                logging.info(f"Windows ACL set for: {file_path}")
            except ImportError:
                # If pywin32 is not available, set basic hidden attribute only
                try:
                    import ctypes
                    ctypes.windll.kernel32.SetFileAttributesW(file_path, 2)  # FILE_ATTRIBUTE_HIDDEN
                    logging.warning(f"Set hidden attribute for: {file_path} (install pywin32 for better security)")
                except:
                    logging.warning(f"Could not set Windows file attributes for: {file_path}")
        else:  # Unix/Linux/Mac
            # 600 permissions (owner read/write only)
            os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)
            logging.info(f"Unix permissions set to 600 for: {file_path}")

    except Exception as e:
        logging.error(f"Failed to set secure file permissions for {file_path}: {e}")
        # Treat permission setting failure as a fatal error
        raise SecurityError(f"Cannot secure file permissions: {e}")

# Clean up old token files
def cleanup_old_tokens():
    """Auto-delete token files older than 1 day"""
    try:
        # Find all token files
        token_patterns = ["KIS*.token", "KIS20*"]  # Support multiple patterns
        token_files = []

        for pattern in token_patterns:
            token_files.extend(Path(config_root).glob(pattern))

        now = datetime.now()
        for token_file in token_files:
            # Check file modification time
            file_mtime = datetime.fromtimestamp(token_file.stat().st_mtime)
            age_days = (now - file_mtime).days

            # Delete files older than 1 day
            if age_days > 1:
                try:
                    os.remove(token_file)
                    logging.info(f"Cleaned up old token: {token_file}")
                except Exception as e:
                    logging.warning(f"Could not delete old token: {e}")

    except Exception as e:
        logging.error(f"Error during token cleanup: {e}")


# ============== Credential Validation ==============
def validate_credentials(app_key: str, mode: str) -> Tuple[bool, str]:
    """
    Validate app key matches the trading mode to prevent credential mismatch errors.

    - Real mode (prod): App key should start with 'PS' but NOT 'PSVT'
    - Demo mode (vps): App key should start with 'PSVT'

    Args:
        app_key: The KIS app key
        mode: 'prod' for real trading, 'vps' for paper trading

    Returns:
        Tuple of (is_valid, error_message)
    """
    if not app_key or len(app_key) < 10:
        return False, "App key is empty or too short"

    is_demo_key = app_key.startswith('PSVT')

    if mode == 'prod' and is_demo_key:
        return False, (
            "CREDENTIAL MISMATCH! Using DEMO app key (PSVT*) in REAL mode.\n"
            "Check kis_devlp.yaml - 'my_app' should be your real trading key (PS*, not PSVT*).\n"
            "This is the most common cause of 'Error Code: 500' authentication failures."
        )

    if mode == 'vps' and not is_demo_key and app_key.startswith('PS'):
        return False, (
            "CREDENTIAL MISMATCH! Using REAL app key (PS*) in DEMO mode.\n"
            "Check kis_devlp.yaml - 'paper_app' should be your demo key (PSVT*).\n"
            "Using real credentials in demo mode may cause unexpected behavior."
        )

    return True, ""


# ============== Cross-Platform File Lock ==============
class CrossPlatformFileLock:
    """Cross-platform file lock using atomic file creation (works on Windows and Unix)"""

    def __init__(self, lock_path: str, timeout: float = 30.0):
        self.lock_path = Path(lock_path)
        self.timeout = timeout
        self._lock_fd = None

    def acquire(self) -> bool:
        """Attempt to acquire lock within timeout"""
        start_time = time.time()

        while time.time() - start_time < self.timeout:
            try:
                # O_CREAT | O_EXCL: Only create if file doesn't exist (atomic)
                self._lock_fd = os.open(
                    str(self.lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
                os.write(self._lock_fd, str(os.getpid()).encode())
                return True
            except FileExistsError:
                # Lock file exists - check if stale (older than 5 minutes)
                try:
                    lock_age = time.time() - self.lock_path.stat().st_mtime
                    if lock_age > 300:  # 5 minutes
                        logging.warning(f"Removing stale lock file: {self.lock_path}")
                        self.lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                time.sleep(0.1)
            except OSError as e:
                logging.warning(f"Lock acquisition error: {e}")
                time.sleep(0.1)
        return False

    def release(self):
        """Release the lock"""
        if self._lock_fd is not None:
            try:
                os.close(self._lock_fd)
            except:
                pass
            self._lock_fd = None
        try:
            self.lock_path.unlink()
        except:
            pass

    def __enter__(self):
        if not self.acquire():
            raise TokenFileError(f"Could not acquire file lock: {self.lock_path}")
        return self

    def __exit__(self, *args):
        self.release()


# ============== Retry Logic for Token Request ==============
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=60),
    retry=retry_if_exception_type((requests.RequestException,)),
    reraise=True
)
def _request_token_with_retry(url: str, params: dict, headers: dict) -> dict:
    """
    Request token from KIS API with retry logic for transient failures.

    - Retries up to 3 times with exponential backoff (5s, 10s, 20s)
    - Does NOT retry on 401/403 (authentication failures)
    - Raises TokenRequestError on failure
    """
    try:
        res = requests.post(
            url,
            data=json.dumps(params),
            headers=headers,
            timeout=KIS_HTTP_TIMEOUT,
        )
    except requests.RequestException as e:
        logging.warning(f"Token request network error (will retry): {e}")
        raise

    if res.status_code == 200:
        return res.json()

    # Non-retryable authentication errors
    if res.status_code in (401, 403):
        error_msg = f"Authentication failed: HTTP {res.status_code}"
        logging.error(f"{error_msg} - {res.text}")
        raise TokenRequestError(error_msg, res.status_code, res.text)

    # Retryable server errors (500, 502, 503, 504)
    if res.status_code >= 500:
        error_msg = f"Server error: HTTP {res.status_code}"
        logging.warning(f"{error_msg} (will retry) - {res.text}")
        # Raise requests exception to trigger retry
        raise requests.RequestException(error_msg)

    # Other client errors - don't retry
    error_msg = f"Token request failed: HTTP {res.status_code}"
    logging.error(f"{error_msg} - {res.text}")
    raise TokenRequestError(error_msg, res.status_code, res.text)


# ============== Safe File Delete (Windows compatible) ==============
def _safe_delete(file_path: Path, max_retries: int = 3) -> bool:
    """Safely delete a file with retries (handles Windows locked files)"""
    for attempt in range(max_retries):
        try:
            file_path.unlink()
            return True
        except PermissionError:
            if os.name == 'nt':
                # Windows: File may be locked by another process
                time.sleep(0.2 * (attempt + 1))
            else:
                raise
        except FileNotFoundError:
            return True  # Already deleted

    logging.warning(f"Could not delete file (may be in use): {file_path}")
    return False


def _token_file_for_account(account_key: str | None) -> Path | None:
    if not account_key:
        return None
    acct_hash = hashlib.sha256(account_key.encode()).hexdigest()[:8]
    return Path(config_root) / f"KIS_acct_{acct_hash}.token"


def _discard_saved_token(account_key: str | None = None) -> None:
    """Delete saved token files so the next auth call must request a new token."""
    if account_key:
        token_file = _token_file_for_account(account_key)
        if token_file and token_file.exists():
            logging.info("Deleting expired KIS token file for account %s: %s", _mask_account_key(account_key), token_file)
            _safe_delete(token_file)
        return

    token_files = list(Path(config_root).glob("KIS*.token")) + list(Path(config_root).glob("KIS20*"))
    if os.path.exists(token_tmp) and Path(token_tmp) not in token_files:
        token_files.append(Path(token_tmp))
    for token_file in token_files:
        logging.info("Deleting expired KIS token file: %s", token_file)
        _safe_delete(token_file)


# ============== Atomic Write (Cross-platform) ==============
def _atomic_write(file_path_str: str, data: bytes) -> bool:
    """
    Write data to file atomically (write to temp, then rename).
    Works on both Windows and Unix.
    """
    file_path = Path(file_path_str)
    temp_path = None

    try:
        # Write to temp file first
        fd, temp_path = tempfile.mkstemp(
            dir=str(file_path.parent),
            prefix=".tmp_token_"
        )
        try:
            os.write(fd, data)
            os.fsync(fd)  # Ensure data is written to disk
        finally:
            os.close(fd)

        # Set Unix permissions (ignored on Windows)
        if os.name != 'nt':
            os.chmod(temp_path, 0o600)

        # Windows: Must delete target file before rename
        if os.name == 'nt' and file_path.exists():
            for attempt in range(3):
                try:
                    file_path.unlink()
                    break
                except PermissionError:
                    time.sleep(0.1 * (attempt + 1))
            else:
                # If can't delete, try moving to backup
                backup_path = file_path.with_suffix('.old')
                try:
                    shutil.move(str(file_path), str(backup_path))
                except:
                    raise TokenFileError(f"Cannot replace locked file: {file_path}")

        # Atomic rename
        shutil.move(temp_path, str(file_path))
        return True

    except Exception as e:
        # Clean up temp file on failure
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except:
                pass
        raise TokenFileError(f"Atomic write failed: {e}")


# Check token validity time and reissue if expired
def _getBaseHeader():
    if _autoReAuth:
        reAuth()
    headers = copy.deepcopy(_base_headers)  # static fields only (Content-Type, User-Agent, etc.)
    if _TRENV is not None:
        headers["authorization"] = f"Bearer {_TRENV.my_token}"
        headers["appkey"] = _TRENV.my_app
        headers["appsecret"] = _TRENV.my_sec
    return headers


def get_trading_env_lock():
    return _TRENV_LOCK


# Get: App key, App secret, Account number (8 digits), Account product code (2 digits), Token, Domain
def _setTRENV(cfg):
    nt1 = namedtuple(
        "KISEnv",
        ["my_app", "my_sec", "my_acct", "my_prod", "my_htsid", "my_token", "my_url", "my_url_ws"],
    )
    d = {
        "my_app": cfg["my_app"],  # App key
        "my_sec": cfg["my_sec"],  # App secret
        "my_acct": cfg["my_acct"],  # Account number (8 digits)
        "my_prod": cfg["my_prod"],  # Account product code (2 digits)
        "my_htsid": cfg["my_htsid"],  # HTS ID
        "my_token": cfg["my_token"],  # Token
        "my_url": cfg[
            "my_url"
        ],  # Live domain (https://openapi.koreainvestment.com:9443)
        "my_url_ws": cfg["my_url_ws"],
    }  # Demo domain (https://openapivts.koreainvestment.com:29443)

    # print(cfg['my_app'])
    global _TRENV
    _TRENV = nt1(**d)


def isPaperTrading():  # Paper trading
    return _isPaper


# Set 'prod' for live trading, 'vps' for paper trading
def changeTREnv(
    token_key,
    svr="prod",
    product=DEFAULT_PRODUCT_CODE,
    account_name=None,
    account_index=None,
    account_key=None,
):
    cfg = dict()

    global _isPaper, _smartSleep, _CURRENT_AUTH_CONTEXT
    if svr == "prod":  # Live trading
        ak1 = "my_app"  # App key for live trading
        ak2 = "my_sec"  # App secret for live trading
        _isPaper = False
        _smartSleep = 0.05
    elif svr == "vps":  # Paper trading
        ak1 = "paper_app"  # App key for paper trading
        ak2 = "paper_sec"  # App secret for paper trading
        _isPaper = True
        _smartSleep = 0.5

    account = resolve_account(
        svr=svr,
        product=product,
        account_name=account_name,
        account_index=account_index,
        account_key=account_key,
    )

    # Use per-account credentials if configured, fall back to global keys
    cfg["my_app"] = account.get("app_key") or _cfg[ak1]
    cfg["my_sec"] = account.get("app_secret") or _cfg[ak2]

    cfg["my_acct"] = account["account"]
    cfg["my_prod"] = account["product"]
    cfg["my_htsid"] = _cfg["my_htsid"]
    cfg["my_url"] = _cfg[svr]

    try:
        my_token = _TRENV.my_token if _TRENV is not None else ""
    except (AttributeError, TypeError):
        my_token = ""
    cfg["my_token"] = token_key or my_token
    cfg["my_url_ws"] = _cfg["ops" if svr == "prod" else "vops"]

    # print(cfg)
    _setTRENV(cfg)
    _CURRENT_AUTH_CONTEXT = {
        "svr": svr,
        "product": cfg["my_prod"],
        "account_name": account_name,
        "account_index": account_index,
        "account_key": account.get("account_key") or account_key,
        "token_account_key": account_key,
    }


def _getResultObject(json_data):
    _tc_ = namedtuple("res", json_data.keys())

    return _tc_(**json_data)


# Token issuance, validity 1 day, maintains existing token if issued within 6 hours, notification sent on issuance
# For paper trading use svr='vps', if not investment account(01) change product='XX' (last 2 digits of account number)
def auth(
    svr="prod",
    product=DEFAULT_PRODUCT_CODE,
    url=None,
    account_name=None,
    account_index=None,
    account_key=None,
):
    """
    Authenticate with KIS API and obtain access token.

    Improvements in this version:
    - Credential validation (detects demo/real key mismatch)
    - Retry logic for transient network failures
    - Raises exceptions instead of silent failure
    - Auto-cleanup of empty/corrupted token files

    Args:
        svr: 'prod' for real trading, 'vps' for paper trading
        product: Account product code (default from config)
        url: API URL (optional, auto-generated from svr)

    Raises:
        CredentialMismatchError: App key doesn't match trading mode
        TokenRequestError: Failed to obtain token from KIS API
        TokenFileError: Failed to save token to file
    """
    p = {
        "grant_type": "client_credentials",
    }

    # Determine global fallback keys based on server type
    if svr == "prod":  # Live trading
        ak1 = "my_app"
        ak2 = "my_sec"
    elif svr == "vps":  # Paper trading
        ak1 = "paper_app"
        ak2 = "paper_sec"
    else:
        raise ValueError(f"Invalid server type: {svr}. Must be 'prod' or 'vps'")

    # Resolve account early to pick up per-account app_key/app_secret if configured
    try:
        _auth_account = resolve_account(
            svr=svr,
            product=product,
            account_name=account_name,
            account_index=account_index,
            account_key=account_key,
        )
        app_key = _auth_account.get("app_key") or _cfg.get(ak1)
        app_secret = _auth_account.get("app_secret") or _cfg.get(ak2)
    except Exception:
        # Fallback to global keys if account resolution fails at this stage
        app_key = _cfg.get(ak1)
        app_secret = _cfg.get(ak2)
        _auth_account = None

    if not app_key or not app_secret:
        raise CredentialMismatchError(
            f"Missing credentials in kis_devlp.yaml: {ak1}={app_key is not None}, {ak2}={app_secret is not None}"
        )

    # CRITICAL: Validate credential/mode match (Issue #137 root cause)
    is_valid, error_msg = validate_credentials(app_key, svr)
    if not is_valid:
        logging.error(f"❌ {error_msg}")
        raise CredentialMismatchError(error_msg)

    p["appkey"] = app_key
    p["appsecret"] = app_secret

    # Check for existing valid token (per-account if account_key provided)
    saved_token = read_token(account_key=account_key)

    if saved_token is None:
        # No valid token - request new one
        token_url = f"{_cfg[svr]}/oauth2/tokenP"
        logging.info(f"Requesting new token from KIS API ({svr} mode)...")

        try:
            # Use retry logic for transient failures
            result = _request_token_with_retry(token_url, p, _getBaseHeader())

            my_token = result.get("access_token")
            my_expired = result.get("access_token_token_expired")

            if not my_token or not my_expired:
                raise TokenRequestError(
                    "Invalid response from KIS API: missing token or expiry",
                    status_code=200,
                    response_text=str(result)
                )

            # Save the new token (per-account if account_key provided)
            save_token(my_token, my_expired, account_key=account_key)
            logging.info(f"✅ New token obtained and saved (expires: {my_expired})")

        except TokenRequestError as e:
            logging.error(f"❌ Token request failed: {e}")
            logging.error(f"   Status Code: {e.status_code}")
            logging.error(f"   Response: {e.response_text}")
            # Re-raise with clear error message
            raise

        except Exception as e:
            logging.error(f"❌ Unexpected error during token request: {e}")
            raise TokenRequestError(f"Unexpected error: {e}")

    else:
        my_token = saved_token
        logging.info("✅ Using existing valid token")

    # Set up environment with token
    changeTREnv(
        my_token,
        svr,
        product,
        account_name=account_name,
        account_index=account_index,
        account_key=account_key,
    )

    # Update base headers
    if _TRENV is not None:
        _base_headers["authorization"] = f"Bearer {my_token}"
        _base_headers["appkey"] = _TRENV.my_app
        _base_headers["appsecret"] = _TRENV.my_sec

    global _last_auth_time
    _last_auth_time = datetime.now()

    if _DEBUG:
        print(f"[{_last_auth_time}] => get AUTH Key completed!")


# end of initialize, token reissue, token validity 1 day on issuance
# Saves to _last_auth_time on program execution to check validity, reissues token on expiry
def reAuth(svr="prod", product=DEFAULT_PRODUCT_CODE, account_name=None, account_index=None, account_key=None):
    n2 = datetime.now()
    # BUG FIX: Changed .seconds to .total_seconds()
    # .seconds only returns seconds within the current day (0-86399)
    # .total_seconds() returns the total duration in seconds
    # Also reduced to 23 hours (82800s) for safety margin before 24h expiry
    if (n2 - _last_auth_time).total_seconds() >= 82800:  # 23 hours (safety margin)
        logging.info("Token approaching expiry, re-authenticating...")
        auth(svr, product, account_name=account_name, account_index=account_index, account_key=account_key)


def getEnv():
    return _cfg


def smart_sleep():
    if _DEBUG:
        print(f"[RateLimit] Sleeping {_smartSleep}s ")

    time.sleep(_smartSleep)


def getTREnv():
    if _TRENV is None:
        raise RuntimeError("Authentication not completed. Call auth() function first.")
    return _TRENV


# Function to receive hash key value for order API and set it in header
# Currently hash key is not mandatory, can be omitted, used when concerned about tampering during API calls
# Input: HTTP Header, HTTP post param
# Output: None
def set_order_hash_key(h, p):
    url = f"{getTREnv().my_url}/uapi/hashkey"  # hashkey issuance API URL

    res = requests.post(url, data=json.dumps(p), headers=h, timeout=KIS_HTTP_TIMEOUT)
    rescode = res.status_code
    if rescode == 200:
        h["hashkey"] = _getResultObject(res.json()).HASH
    else:
        print("Error:", rescode)


# Common function for processing API call responses
class APIResp:
    def __init__(self, resp):
        self._rescode = resp.status_code
        self._resp = resp
        self._header = self._setHeader()
        self._body = self._setBody()
        self._err_code = self._body.msg_cd
        self._err_message = self._body.msg1

    def getResCode(self):
        return self._rescode

    def _setHeader(self):
        fld = dict()
        for x in self._resp.headers.keys():
            if x.islower():
                fld[x] = self._resp.headers.get(x)
        _th_ = namedtuple("header", fld.keys())

        return _th_(**fld)

    def _setBody(self):
        _tb_ = namedtuple("body", self._resp.json().keys())

        return _tb_(**self._resp.json())

    def getHeader(self):
        return self._header

    def getBody(self):
        return self._body

    def getResponse(self):
        return self._resp

    def isOK(self):
        try:
            if self.getBody().rt_cd == "0":
                return True
            else:
                return False
        except:
            return False

    def getErrorCode(self):
        return self._err_code

    def getErrorMessage(self):
        return self._err_message

    def printAll(self):
        print("<Header>")
        for x in self.getHeader()._fields:
            print(f"\t-{x}: {getattr(self.getHeader(), x)}")
        print("<Body>")
        for x in self.getBody()._fields:
            print(f"\t-{x}: {getattr(self.getBody(), x)}")

    def printError(self, url):
        print(
            "-------------------------------\nError in response: ",
            self.getResCode(),
            " url=",
            url,
        )
        print(
            "rt_cd : ",
            self.getBody().rt_cd,
            "/ msg_cd : ",
            self.getErrorCode(),
            "/ msg1 : ",
            self.getErrorMessage(),
        )
        print("-------------------------------")

    # end of class APIResp


class APIRespError(APIResp):
    def __init__(self, status_code, error_text):
        # Initialize directly without calling parent constructor
        self.status_code = status_code
        self.error_text = error_text
        self._error_code = str(status_code)
        self._error_message = error_text
        try:
            payload = json.loads(error_text) if isinstance(error_text, str) else {}
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict):
            self._error_code = str(payload.get("msg_cd") or payload.get("code") or status_code)
            self._error_message = str(payload.get("msg1") or payload.get("message") or error_text)

    def isOK(self):
        return False

    def getErrorCode(self):
        return self._error_code

    def getErrorMessage(self):
        return self._error_message

    def getBody(self):
        # Return empty object (prevents AttributeError on attribute access)
        class EmptyBody:
            def __getattr__(self, name):
                return None

        return EmptyBody()

    def getHeader(self):
        # Return empty object
        class EmptyHeader:
            tr_cont = ""

            def __getattr__(self, name):
                return ""

        return EmptyHeader()

    def printAll(self):
        print(f"=== ERROR RESPONSE ===")
        print(f"Status Code: {self.status_code}")
        print(f"Error Message: {self.error_text}")
        print(f"======================")

    def printError(self, url=""):
        print(f"Error Code : {self.status_code} | {self.error_text}")
        if url:
            print(f"URL: {url}")


def _is_kis_rate_limit_response(res) -> bool:
    try:
        return res.getErrorCode() == KIS_RATE_LIMIT_ERROR_CODE
    except Exception:
        return False


def _is_kis_expired_token_response(res) -> bool:
    try:
        return res.getErrorCode() == KIS_EXPIRED_TOKEN_ERROR_CODE
    except Exception:
        return False


def _refresh_after_expired_token_response() -> None:
    """Force token reissue after KIS reports EGW00123 for the active account."""
    context = dict(_CURRENT_AUTH_CONTEXT)
    account_key = context.get("account_key")
    token_account_key = context.get("token_account_key")
    _discard_saved_token(account_key=token_account_key)
    auth(
        svr=context.get("svr", "prod"),
        product=context.get("product", DEFAULT_PRODUCT_CODE),
        account_name=context.get("account_name"),
        account_index=context.get("account_index"),
        account_key=account_key,
    )


def _kis_retry_delay_seconds(attempt_index: int) -> float:
    base_seconds = max(0.0, KIS_RATE_LIMIT_RETRY_BASE_SECONDS)
    max_seconds = max(base_seconds, KIS_RATE_LIMIT_RETRY_MAX_SECONDS)
    return min(max_seconds, base_seconds * (2 ** max(0, attempt_index - 1)))


def _request_once(url: str, headers: dict[str, Any], params: dict[str, Any], *, postFlag: bool):
    if postFlag:
        return requests.post(
            url,
            headers=headers,
            data=json.dumps(params),
            timeout=KIS_HTTP_TIMEOUT,
        )
    return requests.get(url, headers=headers, params=params, timeout=KIS_HTTP_TIMEOUT)



########### API call wrapping : Common API call


def _url_fetch(
        api_url, ptr_id, tr_cont, params, appendHeaders=None, postFlag=False, hashFlag=True
):
    url = f"{getTREnv().my_url}{api_url}"

    headers = _getBaseHeader()  # Organize basic header values

    # Set additional Headers
    tr_id = ptr_id
    if ptr_id[0] in ("T", "J", "C"):  # Check TR id for live trading
        if isPaperTrading():  # Identify TR id for paper trading
            tr_id = "V" + ptr_id[1:]

    headers["tr_id"] = tr_id  # Transaction TR id
    headers["custtype"] = "P"  # General (individual/corporate customer) "P", affiliate "B"
    headers["tr_cont"] = tr_cont  # Transaction TR id

    if appendHeaders is not None:
        if len(appendHeaders) > 0:
            for x in appendHeaders.keys():
                headers[x] = appendHeaders.get(x)

    if _DEBUG:
        print("< Sending Info >")
        print(f"URL: {url}, TR: {tr_id}")
        print(f"<header>\n{headers}")
        print(f"<body>\n{params}")

    attempts = max(1, KIS_RATE_LIMIT_RETRY_ATTEMPTS)
    expired_token_retry_used = False
    rate_limit_attempt = 1
    while rate_limit_attempt <= attempts:
        res = _request_once(url, headers, params, postFlag=postFlag)

        if res.status_code == 200:
            ar = APIResp(res)
            if _DEBUG:
                ar.printAll()
            return ar

        error_response = APIRespError(res.status_code, res.text)
        if _is_kis_expired_token_response(error_response) and not expired_token_retry_used:
            expired_token_retry_used = True
            logging.warning(
                "KIS API reported expired token %s from %s (TR %s); refreshing token and retrying once",
                KIS_EXPIRED_TOKEN_ERROR_CODE,
                api_url,
                tr_id,
            )
            try:
                _refresh_after_expired_token_response()
            except Exception as refresh_error:
                logging.error("Failed to refresh KIS token after %s: %s", KIS_EXPIRED_TOKEN_ERROR_CODE, refresh_error)
                print("Error Code : " + str(res.status_code) + " | " + res.text)
                return error_response

            headers = _getBaseHeader()
            headers["tr_id"] = tr_id
            headers["custtype"] = "P"
            headers["tr_cont"] = tr_cont
            if appendHeaders is not None:
                for x in appendHeaders.keys():
                    headers[x] = appendHeaders.get(x)
            continue

        if not _is_kis_rate_limit_response(error_response) or rate_limit_attempt >= attempts:
            print("Error Code : " + str(res.status_code) + " | " + res.text)
            return error_response

        delay_seconds = _kis_retry_delay_seconds(rate_limit_attempt)
        logging.warning(
            "KIS API rate limit %s from %s (TR %s); retrying attempt %s/%s after %.2fs",
            KIS_RATE_LIMIT_ERROR_CODE,
            api_url,
            tr_id,
            rate_limit_attempt + 1,
            attempts,
            delay_seconds,
        )
        rate_limit_attempt += 1
        time.sleep(delay_seconds)

    return APIRespError(599, "KIS API retry loop exhausted unexpectedly")


# auth()
# print("Pass through the end of the line")


########### New - websocket support

_base_headers_ws = {
    "content-type": "utf-8",
}


def _getBaseHeader_ws():
    if _autoReAuth:
        reAuth_ws()

    return copy.deepcopy(_base_headers_ws)


def auth_ws(svr="prod", product=DEFAULT_PRODUCT_CODE, account_name=None, account_index=None, account_key=None):
    p = {"grant_type": "client_credentials"}
    if svr == "prod":
        ak1 = "my_app"
        ak2 = "my_sec"
    elif svr == "vps":
        ak1 = "paper_app"
        ak2 = "paper_sec"

    p["appkey"] = _cfg[ak1]
    p["secretkey"] = _cfg[ak2]

    url = f"{_cfg[svr]}/oauth2/Approval"
    res = requests.post(
        url,
        data=json.dumps(p),
        headers=_getBaseHeader(),
        timeout=KIS_HTTP_TIMEOUT,
    )  # Token issuance
    rescode = res.status_code
    if rescode == 200:  # Token issued successfully
        approval_key = _getResultObject(res.json()).approval_key
    else:
        print("Get Approval token fail!\nYou have to restart your app!!!")
        return

    changeTREnv(None, svr, product, account_name=account_name, account_index=account_index, account_key=account_key)

    _base_headers_ws["approval_key"] = approval_key

    global _last_auth_time
    _last_auth_time = datetime.now()

    if _DEBUG:
        print(f"[{_last_auth_time}] => get AUTH Key completed!")


def reAuth_ws(svr="prod", product=DEFAULT_PRODUCT_CODE, account_name=None, account_index=None, account_key=None):
    n2 = datetime.now()
    if (n2 - _last_auth_time).total_seconds() >= 82800:
        auth_ws(svr, product, account_name=account_name, account_index=account_index, account_key=account_key)


def data_fetch(tr_id, tr_type, params, appendHeaders=None) -> dict:
    headers = _getBaseHeader_ws()  # Organize basic header values

    headers["tr_type"] = tr_type
    headers["custtype"] = "P"

    if appendHeaders is not None:
        if len(appendHeaders) > 0:
            for x in appendHeaders.keys():
                headers[x] = appendHeaders.get(x)

    if _DEBUG:
        print("< Sending Info >")
        print(f"TR: {tr_id}")
        print(f"<header>\n{headers}")

    inp = {
        "tr_id": tr_id,
    }
    inp.update(params)

    return {"header": headers, "body": {"input": inp}}


# Return iv, ekey, encrypt in dict so they can be saved to each function method file
def system_resp(data):
    isPingPong = False
    isUnSub = False
    isOk = False
    tr_msg = None
    tr_key = None
    encrypt, iv, ekey = None, None, None

    rdic = json.loads(data)

    tr_id = rdic["header"]["tr_id"]
    if tr_id != "PINGPONG":
        tr_key = rdic["header"]["tr_key"]
        encrypt = rdic["header"]["encrypt"]
    if rdic.get("body", None) is not None:
        isOk = True if rdic["body"]["rt_cd"] == "0" else False
        tr_msg = rdic["body"]["msg1"]
        # Extract key for decryption
        if "output" in rdic["body"]:
            iv = rdic["body"]["output"]["iv"]
            ekey = rdic["body"]["output"]["key"]
        isUnSub = True if tr_msg[:5] == "UNSUB" else False
    else:
        isPingPong = True if tr_id == "PINGPONG" else False

    nt2 = namedtuple(
        "SysMsg",
        [
            "isOk",
            "tr_id",
            "tr_key",
            "isUnSub",
            "isPingPong",
            "tr_msg",
            "iv",
            "ekey",
            "encrypt",
        ],
    )
    d = {
        "isOk": isOk,
        "tr_id": tr_id,
        "tr_key": tr_key,
        "tr_msg": tr_msg,
        "isUnSub": isUnSub,
        "isPingPong": isPingPong,
        "iv": iv,
        "ekey": ekey,
        "encrypt": encrypt,
    }

    return nt2(**d)


def aes_cbc_base64_dec(key, iv, cipher_text):
    if key is None or iv is None:
        raise AttributeError("key and iv cannot be None")

    cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
    return bytes.decode(unpad(cipher.decrypt(b64decode(cipher_text)), AES.block_size))


#####
open_map: dict = {}


def add_open_map(
        name: str,
        request: Callable[[str, str, ...], (dict, list[str])],
        data: str | list[str],
        kwargs: dict = None,
):
    if open_map.get(name, None) is None:
        open_map[name] = {
            "func": request,
            "items": [],
            "kwargs": kwargs,
        }

    if type(data) is list:
        open_map[name]["items"] += data
    elif type(data) is str:
        open_map[name]["items"].append(data)


data_map: dict = {}


def add_data_map(
        tr_id: str,
        columns: list = None,
        encrypt: str = None,
        key: str = None,
        iv: str = None,
):
    if data_map.get(tr_id, None) is None:
        data_map[tr_id] = {"columns": [], "encrypt": False, "key": None, "iv": None}

    if columns is not None:
        data_map[tr_id]["columns"] = columns

    if encrypt is not None:
        data_map[tr_id]["encrypt"] = encrypt

    if key is not None:
        data_map[tr_id]["key"] = key

    if iv is not None:
        data_map[tr_id]["iv"] = iv


class KISWebSocket:
    api_url: str = ""
    on_result: Callable[
        [websockets.ClientConnection, str, pd.DataFrame, dict], None
    ] = None
    result_all_data: bool = False

    retry_count: int = 0
    amx_retries: int = 0

    # init
    def __init__(self, api_url: str, max_retries: int = 3):
        self.api_url = api_url
        self.max_retries = max_retries

    # private
    async def __subscriber(self, ws: websockets.ClientConnection):
        async for raw in ws:
            logging.info("received message >> %s" % raw)
            show_result = False

            df = pd.DataFrame()

            if raw[0] in ["0", "1"]:
                d1 = raw.split("|")
                if len(d1) < 4:
                    raise ValueError("data not found...")

                tr_id = d1[1]

                dm = data_map[tr_id]
                d = d1[3]
                if dm.get("encrypt", None) == "Y":
                    d = aes_cbc_base64_dec(dm["key"], dm["iv"], d)

                df = pd.read_csv(
                    StringIO(d), header=None, sep="^", names=dm["columns"], dtype=object
                )

                show_result = True

            else:
                rsp = system_resp(raw)

                tr_id = rsp.tr_id
                add_data_map(
                    tr_id=rsp.tr_id, encrypt=rsp.encrypt, key=rsp.ekey, iv=rsp.iv
                )

                if rsp.isPingPong:
                    print(f"### RECV [PINGPONG] [{raw}]")
                    await ws.pong(raw)
                    print(f"### SEND [PINGPONG] [{raw}]")

                if self.result_all_data:
                    show_result = True

            if show_result is True and self.on_result is not None:
                self.on_result(ws, tr_id, df, data_map[tr_id])

    async def __runner(self):
        if len(open_map.keys()) > 40:
            raise ValueError("Subscription's max is 40")

        url = f"{getTREnv().my_url_ws}{self.api_url}"

        while self.retry_count < self.max_retries:
            try:
                async with websockets.connect(url) as ws:
                    # request subscribe
                    for name, obj in open_map.items():
                        await self.send_multiple(
                            ws, obj["func"], "1", obj["items"], obj["kwargs"]
                        )

                    # subscriber
                    await asyncio.gather(
                        self.__subscriber(ws),
                    )
            except Exception as e:
                print("Connection exception >> ", e)
                self.retry_count += 1
                await asyncio.sleep(1)

    # func
    @classmethod
    async def send(
            cls,
            ws: websockets.ClientConnection,
            request: Callable[[str, str, ...], (dict, list[str])],
            tr_type: str,
            data: str,
            kwargs: dict = None,
    ):
        k = {} if kwargs is None else kwargs
        msg, columns = request(tr_type, data, **k)

        add_data_map(tr_id=msg["body"]["input"]["tr_id"], columns=columns)

        logging.info("send message >> %s" % json.dumps(msg))

        await ws.send(json.dumps(msg))
        smart_sleep()

    async def send_multiple(
            self,
            ws: websockets.ClientConnection,
            request: Callable[[str, str, ...], (dict, list[str])],
            tr_type: str,
            data: list | str,
            kwargs: dict = None,
    ):
        if type(data) is str:
            await self.send(ws, request, tr_type, data, kwargs)
        elif type(data) is list:
            for d in data:
                await self.send(ws, request, tr_type, d, kwargs)
        else:
            raise ValueError("data must be str or list")

    @classmethod
    def subscribe(
            cls,
            request: Callable[[str, str, ...], (dict, list[str])],
            data: list | str,
            kwargs: dict = None,
    ):
        add_open_map(request.__name__, request, data, kwargs)

    def unsubscribe(
            self,
            ws: websockets.ClientConnection,
            request: Callable[[str, str, ...], (dict, list[str])],
            data: list | str,
    ):
        self.send_multiple(ws, request, "2", data)

    # start
    def start(
            self,
            on_result: Callable[
                [websockets.ClientConnection, str, pd.DataFrame, dict], None
            ],
            result_all_data: bool = False,
    ):
        self.on_result = on_result
        self.result_all_data = result_all_data
        try:
            asyncio.run(self.__runner())
        except KeyboardInterrupt:
            print("Closing by KeyboardInterrupt")
