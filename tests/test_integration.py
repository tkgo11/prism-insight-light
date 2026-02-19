import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from trading.domestic_stock_trading import AsyncTradingContext, DomesticStockTrading
from trading.us_stock_trading import AsyncUSTradingContext, USStockTrading
from tests.mock_kis_api import MockKISApi

@pytest.mark.asyncio
async def test_domestic_buy_integration():
    # Patch the global auth_manager instance in kis_auth
    with patch("trading.kis_auth._auth_manager") as mock_auth_mgr:
        # Setup mock behavior
        # Mock Env object
        class MockEnv:
            my_acct = "12345678"
            my_prod = "01"
            
        mock_auth_mgr.async_url_fetch = mock_api.async_url_fetch
        mock_auth_mgr.get_tr_env.return_value = MockEnv()
        mock_auth_mgr.close = AsyncMock()
        
        async with AsyncTradingContext(mode="demo", buy_amount=1000000, auto_trading=True) as trader:
            res = await trader.async_buy_stock("005930", limit_price=71000)
            
            assert res.success
            assert res.order_no == "0000112345"

@pytest.mark.asyncio
async def test_us_buy_integration():
    with patch("trading.kis_auth._auth_manager") as mock_auth_mgr:
        # Mock Env object
        class MockEnv:
            my_acct = "12345678"
            my_prod = "01"

        mock_api = MockKISApi()
        mock_auth_mgr.async_url_fetch = mock_api.async_url_fetch
        mock_auth_mgr.get_tr_env.return_value = MockEnv()
        mock_auth_mgr.close = AsyncMock()
        
        async with AsyncUSTradingContext(mode="demo", buy_amount=1000.0, auto_trading=True) as trader:
            res = await trader.async_buy_stock("AAPL", limit_price=160.0)
            
            assert res.success
            assert res.order_no == "0000112345"
