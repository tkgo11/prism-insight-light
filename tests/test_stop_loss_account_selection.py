"""Stop-loss inquiries must use the same account as order dispatch."""

from types import SimpleNamespace

import pytest

from trading import stop_loss_watcher as watcher_module
from trading.stop_loss_watcher import StopLossWatcher, StopLossWatcherConfig


@pytest.mark.parametrize("market,constructor", [("KR", "DomesticStockTrading"), ("US", "USStockTrading")])
@pytest.mark.parametrize("selector", [{"account_name": "secondary"}, {"account_index": 1}])
def test_watcher_passes_selected_single_account_to_broker(monkeypatch, tmp_path, market, constructor, selector):
    captured = []
    monkeypatch.setattr(watcher_module, constructor, lambda **kwargs: captured.append(kwargs))
    dispatcher = SimpleNamespace(
        trading_mode="demo", multi_account_enabled=False,
        account_name=selector.get("account_name"), account_index=selector.get("account_index"),
    )
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(storage_path=tmp_path / "positions.json"),
    )
    watcher._get_trader(market)
    assert captured == [{"mode": "demo", **selector}]


@pytest.mark.parametrize("market,constructor", [("KR", "MultiAccountDomesticStockTrading"), ("US", "MultiAccountUSStockTrading")])
def test_watcher_multi_account_inquiry_still_covers_all_accounts(monkeypatch, tmp_path, market, constructor):
    captured = []
    monkeypatch.setattr(watcher_module, constructor, lambda **kwargs: captured.append(kwargs))
    dispatcher = SimpleNamespace(trading_mode="real", multi_account_enabled=True)
    watcher = StopLossWatcher(
        dispatcher,
        StopLossWatcherConfig(storage_path=tmp_path / "positions.json"),
    )
    watcher._get_trader(market)
    assert captured == [{"mode": "real"}]
