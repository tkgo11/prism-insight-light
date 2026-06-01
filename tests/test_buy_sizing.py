from trading.buy_sizing import build_buy_sizing, resolve_buy_amount


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
