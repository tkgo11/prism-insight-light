"""Offline submission identity is provenance, never execution authority."""
import asyncio
from types import SimpleNamespace

import pytest

from trading.us import USStockTrading


@pytest.fixture
def trader(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unmocked broker/authentication boundary")
    monkeypatch.setattr("trading.us.ka.auth", forbidden)
    monkeypatch.setattr("trading.us.ka._url_fetch", forbidden)
    value = USStockTrading.__new__(USStockTrading)
    value.auto_trading = True
    value.mode = "demo"
    value.trenv = SimpleNamespace(my_acct="00000000", my_prod="01")
    value._resolve_exchange_code = lambda *args: "NYSE"
    value._resolve_buy_amount = lambda *args: 150.0
    value._resolve_orderable_usd = lambda *args: (150.0, {})
    value._calculate_buy_quantity_inputs = lambda *args: (3, {})
    value.get_holding_quantity = lambda *args: 3
    value.is_reserved_order_available = lambda: True
    value.get_current_price = lambda *args: {"current_price": 50.0, "exchange": "NYSE"}
    value.get_portfolio = lambda: [{"ticker": "IBM", "quantity": 9, "exchange": "NYSE"}]
    value._stock_locks = {}
    value._semaphore = asyncio.Semaphore(1)
    value._global_lock = asyncio.Lock()
    value._request = forbidden
    async def no_sleep(*args):
        pass
    monkeypatch.setattr("trading.us.asyncio.sleep", no_sleep)
    return value


ROUTES = [
    ("buy_market_price", "broker_order", "ODNO"),
    ("buy_limit_price", "broker_order", "ODNO"),
    ("sell_all_market_price", "broker_order", "ODNO"),
    ("buy_reserved_order", "reservation", "ODNO"),
    ("buy_reserved_order", "reservation", "RSVN_ORD_SEQ"),
    ("sell_reserved_order", "reservation", "ODNO"),
    ("sell_reserved_order", "reservation", "RSVN_ORD_SEQ"),
]


@pytest.mark.parametrize("method,kind,receipt_field", ROUTES)
@pytest.mark.parametrize("outcome", ["accepted", "unknown", "rejected"])
def test_us_submission_identity_comes_from_endpoint_and_actual_request(
    trader, method, kind, receipt_field, outcome,
):
    requests = []
    def request(path, tr_id, params, **kwargs):
        requests.append((path, dict(params)))
        if outcome == "unknown":
            raise TimeoutError("mock transport timeout after submission")
        return SimpleNamespace(
            isOK=lambda: outcome == "accepted",
            getBody=lambda: SimpleNamespace(output={receipt_field: "00001234"}),
            getResCode=lambda: 200,
            getErrorCode=lambda: "APBK0918",
            getErrorMessage=lambda: "mock verified rejection",
        )
    trader._request = request
    kwargs = {} if method == "buy_market_price" else {"limit_price": 50.0}
    result = getattr(trader, method)("IBM", **kwargs)
    assert len(requests) == 1
    path, params = requests[0]
    assert ("order-resv" in path) is (kind == "reservation")
    assert result.get("order_kind") == kind
    assert result.get("order_quantity") == int(params.get("ORD_QTY", params.get("FT_ORD_QTY"))) == 3
    assert type(result["order_quantity"]) is int
    assert result.get("order_venue") == params["OVRS_EXCG_CD"] == "NYSE"
    assert result["success"] is (outcome == "accepted")
    if outcome != "accepted":
        assert result["outcome_unknown"] is (outcome == "unknown")
    assert result.get("order_date") is None
    assert "filled_quantity" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("kind", ["broker_order", "reservation"])
@pytest.mark.parametrize("unknown", [False, True])
async def test_us_async_preserves_submission_identity_including_unknown(
    trader, side, kind, unknown,
):
    receipt = {"success": not unknown, "outcome_unknown": unknown,
               "order_no": "00001234", "quantity": 3,
               "order_kind": kind, "order_quantity": 3, "order_venue": "NYSE",
               "message": "mock acceptance, not fill"}
    if side == "buy":
        trader.smart_buy = lambda *args, **kwargs: dict(receipt)
        result = await trader.async_buy_stock("IBM", limit_price=50.0)
    else:
        trader.smart_sell_all = lambda *args, **kwargs: dict(receipt)
        result = await trader.async_sell_stock("IBM", limit_price=50.0)
    assert result.get("order_kind") == kind
    assert result.get("order_quantity") == 3
    assert result.get("order_venue") == "NYSE"
    assert result["success"] is (not unknown)
    if unknown:
        assert result["outcome_unknown"] is True
    assert "filled_quantity" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("side", ["buy", "sell"])
@pytest.mark.parametrize("metadata", [
    {}, {"order_kind": "broker_order"}, {"order_quantity": 3},
    {"order_kind": "reservation", "order_quantity": True},
    {"order_kind": "broker_order", "order_quantity": "3"},
    {"order_kind": "filled", "order_quantity": 3},
])
async def test_us_async_does_not_infer_identity_from_estimated_quantity(trader, side, metadata):
    receipt = {"success": True, "order_no": "00001234", "quantity": 9,
               "message": "mock acceptance only", **metadata}
    if side == "buy":
        trader.smart_buy = lambda *args, **kwargs: dict(receipt)
        result = await trader.async_buy_stock("IBM", limit_price=50.0)
    else:
        trader.smart_sell_all = lambda *args, **kwargs: dict(receipt)
        result = await trader.async_sell_stock("IBM", limit_price=50.0)
    assert result["success"] is True
    assert "order_kind" not in result
    assert "order_quantity" not in result
    assert "order_venue" not in result
