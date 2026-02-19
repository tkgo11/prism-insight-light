
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
import sys
from pathlib import Path

# Add project root
PROJECT_ROOT = Path("c:/Users/tkgo1/prism-insight-light")
sys.path.append(str(PROJECT_ROOT))

from trading.us_stock_trading import USStockTrading
from trading.kis_auth import KISAuthManager, APIResp
from trading.models import OrderResult, StockPrice

class TestUSStockTradingRefactor(unittest.TestCase):
    def setUp(self):
        self.mock_auth = MagicMock(spec=KISAuthManager)
        self.mock_auth.get_tr_env.return_value = MagicMock(my_acct="12345678", my_prod="01")
        self.trader = USStockTrading(self.mock_auth, mode="demo", buy_amount=100.0)

    def test_get_current_price_success(self):
        # Mock API response
        mock_resp = MagicMock(spec=APIResp)
        mock_resp.isOK.return_value = True
        mock_resp.getBody.return_value.output = {
            "last": "150.00", "name": "Apple Inc", "rate": "1.5", "tvol": "1000", "base": "148.00"
        }
        self.mock_auth.url_fetch.return_value = mock_resp

        price = self.trader.get_current_price("AAPL")
        
        self.assertIsNotNone(price)
        self.assertEqual(price.stock_code, "AAPL")
        self.assertEqual(price.current_price, 150.0)
        self.assertEqual(price.exchange, "NASD") # Auto-detected

    def test_buy_market_price(self):
        mock_resp = MagicMock(spec=APIResp)
        mock_resp.isOK.return_value = True
        mock_resp.getBody.return_value.output = {"ODNO": "900123"}
        self.mock_auth.url_fetch.return_value = mock_resp

        # Test logic
        res = self.trader.buy_market_price("AAPL", 2)
        
        self.assertTrue(res["success"])
        self.assertEqual(res["order_no"], "900123")
        self.assertEqual(res["quantity"], 2)
        
        # Verify API call args
        args = self.mock_auth.url_fetch.call_args
        self.assertIn("/uapi/overseas-stock/v1/trading/order", args[0])
        self.assertEqual(args[0][3]["ORD_QTY"], "2")

    def test_calculate_buy_quantity(self):
        # Mock price to 150
        with patch.object(self.trader, 'get_current_price') as mock_get_price:
            mock_get_price.return_value = StockPrice(stock_code="AAPL", stock_name="Apple", current_price=150.0, change_rate=0.0)
            
            qty = self.trader.calculate_buy_quantity("AAPL", 400.0)
            self.assertEqual(qty, 2) # floor(400/150) = 2

    def test_async_buy_stock(self):
         # Mock execute_buy_logic to return success (since we test wrapper)
         # But better to test full flow via mocking internal methods
         
         async def run_test():
             with patch.object(self.trader, 'get_current_price') as mock_get_price, \
                  patch.object(self.trader, 'buy_market_price') as mock_buy:
                 
                 mock_get_price.return_value = StockPrice(stock_code="AAPL", stock_name="Apple", current_price=100.0, change_rate=0)
                 mock_buy.return_value = OrderResult(success=True, order_no="123", quantity=1, message="OK")
                 
                 # 100.0 amount / 100.0 price = 1 share
                 res = await self.trader.async_buy_stock("AAPL", buy_amount=100.0)
                 
                 self.assertTrue(res.success)
                 self.assertEqual(res.quantity, 1)
                 self.assertEqual(res.order_no, "123")
                 
         asyncio.run(run_test())

if __name__ == '__main__':
    unittest.main()
