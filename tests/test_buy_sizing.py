import pytest

from trading.buy_sizing import (
    build_buy_sizing,
    normalize_amount,
    normalize_percent,
    resolve_buy_amount,
)


def test_percent_buy_amount_uses_total_assets_including_holdings():
    sizing = build_buy_sizing(fixed_amount=10000, asset_percent=2.5)

    amount = resolve_buy_amount(
        sizing,
        account_summary={"total_eval_amount": 1_000_000, "available_amount": 900_000},
        fallback_amount=10_000,
        currency="KRW",
    )

    assert amount == 25_000


def test_percent_buy_amount_caps_to_available_cash():
    sizing = build_buy_sizing(fixed_amount=100, asset_percent=10)

    amount = resolve_buy_amount(
        sizing,
        account_summary={"total_eval_amount": 5_000, "available_amount": 300},
        fallback_amount=100,
        currency="USD",
    )

    assert amount == 300


def test_fixed_buy_amount_remains_default_when_percent_unset():
    sizing = build_buy_sizing(fixed_amount=12345, asset_percent=None)

    assert resolve_buy_amount(sizing, account_summary=None, fallback_amount=999, currency="KRW") == 12345


@pytest.mark.parametrize("value", ["inf", "-inf", "nan"])
def test_non_finite_fixed_amount_is_not_accepted(value):
    assert normalize_amount(value) is None


@pytest.mark.parametrize("value", ["inf", "-inf", "nan"])
def test_non_finite_percent_is_rejected(value):
    with pytest.raises(ValueError, match="buy percent"):
        normalize_percent(value)


@pytest.mark.parametrize("value", [True, False])
def test_boolean_fixed_and_percent_buy_config_fail_closed(value):
    with pytest.raises(ValueError, match="boolean"):
        build_buy_sizing(fixed_amount=value)
    with pytest.raises(ValueError, match="boolean"):
        build_buy_sizing(asset_percent=value)


def test_gigantic_buy_config_cannot_raise_unhandled_float_overflow():
    assert normalize_amount(10**400) is None
    with pytest.raises(ValueError, match="finite"):
        normalize_percent(10**400)


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("source", ["configured", "override"])
def test_trader_constructor_rejects_boolean_buy_amount_before_auth(monkeypatch, market, source):
    if market == "KR":
        from trading import domestic as module
        cls = module.DomesticStockTrading
        field = "buy_amount_krw"
    else:
        from trading import us as module
        cls = module.USStockTrading
        field = "buy_amount_usd"
    config = {
        "name": "mock-account", "account_key": "vps:mock-account:01",
        "product": "01", field: True if source == "configured" else 100,
    }
    monkeypatch.setattr(module.ka, "resolve_account", lambda **kwargs: config)
    kwargs = {"mode": "demo"}
    if source == "override":
        kwargs["buy_amount"] = True
    with pytest.raises(ValueError, match="boolean"):
        cls(**kwargs)


@pytest.mark.parametrize("field", ["default_unit_amount", "default_unit_amount_usd"])
@pytest.mark.parametrize("value", [True, False])
def test_legacy_default_buy_amount_rejects_yaml_boolean(field, value):
    from trading.kis_auth import _reject_boolean_buy_default
    with pytest.raises(ValueError, match=field):
        _reject_boolean_buy_default(value, field)


@pytest.mark.parametrize("field", ["buy_amount_krw", "buy_amount_usd"])
@pytest.mark.parametrize("value", [True, False])
def test_account_config_rejects_boolean_amount_even_when_falsy(field, value):
    from trading.kis_auth import _build_normalized_account
    account = {
        "name": "mock", "account": "12345678", "mode": "demo",
        "market": "kr", field: value,
    }
    with pytest.raises(ValueError, match="boolean"):
        _build_normalized_account(0, account)
