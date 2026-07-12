"""Safe account/config view models for the WebUI."""

from __future__ import annotations

import math
import os
import tempfile
from pathlib import Path
from typing import Any

from trading import yaml_compat as yaml

from trading.schema import infer_market
from webui.services.masking import mask_secret_value

CONFIG_PATH = Path("trading") / "config" / "kis_devlp.yaml"
EXAMPLE_CONFIG_PATH = Path("trading") / "config" / "kis_devlp.yaml.example"
SECRET_FIELDS = {"app_key", "app_secret", "my_app", "my_sec", "paper_app", "paper_sec", "my_token"}
EDITABLE_TOP_LEVEL_FIELDS: dict[str, type] = {
    "default_mode": str,
    "auto_trading": bool,
    "default_unit_amount": int,
    "default_unit_amount_usd": float,
    "default_unit_asset_percent": float,
    "default_unit_asset_percent_usd": float,
    "auto_exchange_usd_on_buy": bool,
    "max_auto_exchange_krw": float,
    "auto_exchange_min_shortfall_usd": float,
}


def active_config_path() -> Path:
    return CONFIG_PATH if CONFIG_PATH.exists() else EXAMPLE_CONFIG_PATH


def load_config() -> dict[str, Any]:
    path = active_config_path()
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}


def save_config(data: dict[str, Any]) -> Path:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, indent=2)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=CONFIG_PATH.parent,
            prefix=f".{CONFIG_PATH.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            os.chmod(temporary_path, 0o600)
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, CONFIG_PATH)
        os.chmod(CONFIG_PATH, 0o600)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return CONFIG_PATH


def _mask_account(account: str | None, product: str | None = None) -> str:
    if not account:
        return "missing"
    base = mask_secret_value(str(account), keep=2)
    return f"{base}-{product}" if product else base


def _safe_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def list_accounts() -> dict[str, Any]:
    data = load_config()
    accounts = data.get("accounts") if isinstance(data.get("accounts"), list) else []
    safe_accounts: list[dict[str, Any]] = []
    for index, account in enumerate(accounts):
        if not isinstance(account, dict):
            continue
        market = str(account.get("market") or "all").lower()
        mode = str(account.get("mode") or data.get("default_mode") or "demo").lower()
        product = str(account.get("product") or data.get("default_product_code") or "01")
        account_number = str(account.get("account") or "")
        safe_accounts.append(
            {
                "index": index,
                "name": str(account.get("name") or f"account-{index + 1}"),
                "mode": mode,
                "market": market,
                "market_label": "KR + US" if market in {"all", "both"} else market.upper(),
                "product": product,
                "account_masked": _mask_account(account_number, product),
                "primary": bool(account.get("primary")),
                "buy_amount_krw": _safe_scalar(account.get("buy_amount_krw")),
                "buy_amount_usd": _safe_scalar(account.get("buy_amount_usd")),
                "buy_percent_krw": _safe_scalar(account.get("buy_percent_krw")),
                "buy_percent_usd": _safe_scalar(account.get("buy_percent_usd")),
                "auto_exchange_usd_on_buy": bool(account.get("auto_exchange_usd_on_buy", data.get("auto_exchange_usd_on_buy", False))),
                "has_account_key": bool(account.get("app_key") or account.get("app_secret")),
            }
        )
    return {
        "ok": True,
        "path": str(active_config_path()),
        "path_label": active_config_path().name,
        "using_example": active_config_path() == EXAMPLE_CONFIG_PATH,
        "default_mode": str(data.get("default_mode") or "demo"),
        "auto_trading": bool(data.get("auto_trading")),
        "accounts": safe_accounts,
        "count": len(safe_accounts),
        "error": None,
    }


def get_config_editor_model() -> dict[str, Any]:
    data = load_config()
    safe_fields = []
    for name, expected_type in EDITABLE_TOP_LEVEL_FIELDS.items():
        value = data.get(name)
        safe_fields.append({"name": name, "value": "" if value is None else str(value), "type": expected_type.__name__})
    strategy = data.get("signal_strategy") if isinstance(data.get("signal_strategy"), dict) else {}
    return {
        "ok": True,
        "path_label": active_config_path().name,
        "using_example": active_config_path() == EXAMPLE_CONFIG_PATH,
        "fields": safe_fields,
        "strategy": {
            "name": str(strategy.get("name") or ""),
            "split_count": str(strategy.get("split_count") or "2"),
        },
        "accounts": list_accounts()["accounts"],
    }


def _coerce_value(name: str, raw: str) -> Any:
    expected = EDITABLE_TOP_LEVEL_FIELDS[name]
    text = str(raw).strip()
    if text == "":
        return None
    if expected is bool:
        normalized = text.lower()
        if normalized in {"1", "true", "yes", "y", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "off"}:
            return False
        raise ValueError(f"{name} must be a boolean")
    if expected is int:
        value = int(text)
        if value < 0:
            raise ValueError(f"{name} must not be negative")
        return value
    if expected is float:
        value = float(text)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be a finite non-negative number")
        if "percent" in name and value > 100:
            raise ValueError(f"{name} must be between 0 and 100")
        return value
    if name == "default_mode":
        normalized = text.lower()
        if normalized not in {"demo", "real"}:
            raise ValueError("default_mode must be demo or real")
        return normalized
    return text


def update_config_fields(fields: dict[str, str], strategy: dict[str, str] | None = None) -> dict[str, Any]:
    data = load_config()
    updates: dict[str, Any] = {}
    for name, raw_value in fields.items():
        if name in EDITABLE_TOP_LEVEL_FIELDS:
            updates[name] = _coerce_value(name, raw_value)
    next_strategy: dict[str, Any] | None = None
    if strategy is not None:
        current = data.get("signal_strategy") if isinstance(data.get("signal_strategy"), dict) else {}
        next_strategy = dict(current)
        name = str(strategy.get("name") or "").strip()
        if name not in {"", "balance_split"}:
            raise ValueError("unsupported signal strategy")
        split_count = int(str(strategy.get("split_count") or "2"))
        if split_count <= 0:
            raise ValueError("signal_strategy.split_count must be positive")
        next_strategy["name"] = name
        next_strategy["split_count"] = split_count
    data.update(updates)
    if next_strategy is not None:
        data["signal_strategy"] = next_strategy
    path = save_config(data)
    return {"ok": True, "path_label": path.name, "config": get_config_editor_model(), "error": None}


def build_manual_signal(*, action: str, ticker: str, price: float, company_name: str = "", market: str = "auto") -> dict[str, Any]:
    clean_ticker = ticker.strip().upper()
    resolved_market = infer_market(clean_ticker) if market.lower() == "auto" else market.strip().upper()
    return {
        "type": action.strip().upper(),
        "ticker": clean_ticker,
        "company_name": company_name.strip() or clean_ticker,
        "market": resolved_market,
        "price": price,
    }
