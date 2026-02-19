
import asyncio
import unittest
from unittest.mock import patch, AsyncMock
import logging
import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path("c:/Users/tkgo1/prism-insight-light")
sys.path.append(str(PROJECT_ROOT))

# Import the module to test
# Since subscriber.py is a script, we import specific functions from it
# We need to mock things that subscriber imports if they have side effects on import
# But based on code review, subscriber imports are safe.

from subscriber import execute_us_buy_trade, execute_us_sell_trade

# Configure logger
logger = logging.getLogger("TestUSTrading")
logger.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
logger.addHandler(handler)

class TestUSTradingFlow(unittest.TestCase):

    @patch('subscriber.AsyncUSTradingContext')
    def test_execute_us_buy_trade(self, MockContext):
        print("\nTesting execute_us_buy_trade...")
        # Mock the context manager and trader
        mock_trader = AsyncMock()
        mock_trader.async_buy_stock.return_value = {
            'success': True, 
            'message': 'Buy success', 
            'order_no': '12345'
        }
        
        mock_context_instance = AsyncMock()
        mock_context_instance.__aenter__.return_value = mock_trader
        mock_context_instance.__aexit__.return_value = None
        
        MockContext.return_value = mock_context_instance
        
        # Run executed function
        result = asyncio.run(execute_us_buy_trade("AAPL", "Apple", logger, limit_price=150.0))
        
        # Verify
        MockContext.assert_called_once()
        mock_trader.async_buy_stock.assert_called_once_with("AAPL", limit_price=150.0)
        self.assertTrue(result['success'])
        self.assertEqual(result['order_no'], '12345')
        print("✅ execute_us_buy_trade passed")

    @patch('subscriber.AsyncUSTradingContext')
    def test_execute_us_sell_trade(self, MockContext):
        print("\nTesting execute_us_sell_trade...")
        # Mock the context manager and trader
        mock_trader = AsyncMock()
        mock_trader.async_sell_stock.return_value = {
            'success': True, 
            'message': 'Sell success', 
            'order_no': '67890'
        }
        
        mock_context_instance = AsyncMock()
        mock_context_instance.__aenter__.return_value = mock_trader
        mock_context_instance.__aexit__.return_value = None
        
        MockContext.return_value = mock_context_instance
        
        # Run executed function
        result = asyncio.run(execute_us_sell_trade("TSLA", "Tesla", logger, limit_price=200.0))
        
        # Verify
        MockContext.assert_called_once()
        mock_trader.async_sell_stock.assert_called_once_with("TSLA", limit_price=200.0)
        self.assertTrue(result['success'])
        print("✅ execute_us_sell_trade passed")

if __name__ == '__main__':
    unittest.main()
