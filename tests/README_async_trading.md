# 🧪 Async Trading API Test Guide

> 📖 [한국어 문서](README_async_trading_ko.md)

## 📋 Overview

These test scripts are tools for safely testing the asynchronous API functions in `domestic_stock_trading.py`.

## ⚠️ Important Warnings

- **Always test only in simulation (paper trading) environment**
- Test with small amounts as actual trades may occur
- Check `trading/config/kis_devlp.yaml` configuration first
- Confirmation message appears when selecting real trading mode

## 🚀 Test Script Types

### 1. 🏃‍♂️ `quick_test.py` - Quick Individual Tests

**Purpose**: Use for quick testing of individual functions

**How to Run**:
```bash
cd tests

# View usage
python quick_test.py

# Portfolio inquiry (simulation)
python quick_test.py portfolio

# Buy test (simulation, RF-Tech 10,000 KRW)
python quick_test.py buy

# Sell test (simulation, RF-Tech all shares)
python quick_test.py sell

# Real trading mode (⚠️ Warning!)
python quick_test.py portfolio --mode real
python quick_test.py buy real
python quick_test.py sell --mode real
```

**Features**:
- Quick testing with single command
- argparse-based command line argument support
- Confirmation message for real trading
- Defaults: simulation, RF-Tech (061040), 10,000 KRW

### 2. 🔬 `test_async_trading.py` - Comprehensive Testing

**Purpose**: Use for systematic testing of overall functionality

**How to Run**:
```bash
cd tests
python test_async_trading.py
```

**Features**:
- Interactive menu interface
- Basic tests + batch tests + error handling tests
- Detailed logging and result analysis
- Defaults: RF-Tech (061040), DongKuk S&C (100130), 50,000/30,000 KRW

## 📊 Test Items in Detail

### 🏃‍♂️ Quick Test Items

| Command | Description | Test Content |
|------|------|-------------|
| `portfolio` | Portfolio inquiry | Display holdings, total value, profit/loss, return rate |
| `buy` | Buy test | RF-Tech 10,000 KRW market order buy |
| `sell` | Sell test | RF-Tech all shares market order sell |

### 🔬 Comprehensive Test Items

#### Basic Tests
- ✅ **Portfolio Inquiry**: Check account balance and holdings
- ✅ **Single Buy**: RF-Tech 50,000 KRW market order buy
- ✅ **Single Sell**: RF-Tech all shares market order sell
- ✅ **Error Handling**: Invalid stock code, sell non-owned stock, timeout

#### Batch Tests
- ✅ **Concurrent Buy**: RF-Tech, DongKuk S&C 10,000 KRW each concurrent buy
- ✅ **Concurrent Sell**: All shares of successfully bought stocks concurrent sell
- ✅ **Result Analysis**: Success/failure statistics and detailed logs

## 🖥️ Execution Examples

### Quick Test Examples

```bash
# Simulation portfolio inquiry
(.venv) ➜ python tests/quick_test.py portfolio

🚀 Quick test starting (🟢 Simulation)
========================================
📊 Checking portfolio... (mode: demo)

💼 Holdings: 3 stocks
💰 Total value: 1,234,567 KRW
📈 Total profit: +12,345 KRW
📊 Return: +1.02%
  1. RF-Tech: 10 shares (+2.1%)
  2. DongKuk S&C: 5 shares (-0.5%)
  3. NAVER: 3 shares (+3.2%)

✅ Test completed (Simulation)
```

```bash
# Real trading buy (with confirmation)
(.venv) ➜ python tests/quick_test.py buy --mode real

🚀 Quick test starting (🔴 Real Trading)
========================================
⚠️ Warning: Real trading mode!
⚠️ Actual trades may occur!
========================================
💳 Testing 061040 buy... (Amount: 10,000 KRW, Mode: real)
⚠️ Real trading mode! Actual trades will occur!
Are you sure you want to buy in real trading? (yes/no): no
Buy cancelled.

✅ Test completed (Real Trading)
```

### Comprehensive Test Examples

```bash
(.venv) ➜ python tests/test_async_trading.py

🧪 Async Trading API Test Script
============================================================
⚠️  Warning: Actual trades will occur in real trading mode!
============================================================

Select trading mode:
1. Simulation (demo) - Safe testing
2. Real Trading (real) - ⚠️ Actual trades!

Select mode (1-2): 1
✅ Simulation mode selected

Select test option:
1. Basic tests (portfolio inquiry, single buy/sell, error handling)
2. Batch tests (concurrent buy/sell of multiple stocks)
3. All tests
4. Exit

Select (1-4): 1

🚀 Starting async trading API basic tests (mode: demo)

1️⃣ Portfolio inquiry: Success
📊 Holdings: 2 stocks
💰 Total value: 1,500,000 KRW

2️⃣ Single buy: Success
✅ Buy successful: Buy completed: 8 shares x 62,500 KRW = 500,000 KRW

3️⃣ Single sell: Success
✅ Sell successful: Sell completed: 8 shares (avg price: 62,500 KRW, expected amount: 500,800 KRW, return: +0.48%)

4️⃣ Error handling test: Success
Invalid stock code result: Current price inquiry failed
Sell non-owned stock result: Stock 005490 not in portfolio

✅ Basic tests completed
```

## 🔧 Configuration Changes

### Quick Test Configuration Changes

**Change stock and amount** (edit inside `quick_test.py`):
```python
# Default settings (10,000 KRW, RF-Tech)
await quick_buy_test("061040", 10000, mode)

# Custom (30,000 KRW, Samsung Electronics)
await quick_buy_test("005930", 30000, mode)
```

### Comprehensive Test Configuration Changes

**Change buy amount**:
```python
# During AsyncTradingTester initialization
tester = AsyncTradingTester(mode="demo", buy_amount=100000)  # 100,000 KRW

# Batch test amount
test_tester = AsyncTradingTester(mode=test_mode, buy_amount=50000)  # 50,000 KRW
```

**Change batch test stocks**:
```python
# Default settings
await test_tester.test_batch_operations(["005930", "000660"])

# Custom
await test_tester.test_batch_operations(["005930", "000660", "035420"])  # Add NAVER
```

## 🛡️ Safety Features

### 1. **Default Safety**
- All tests default: `demo` (simulation)
- Small amounts: 10,000-50,000 KRW

### 2. **Real Trading Confirmation**
- Warning message when selecting `real` mode
- Double confirmation message (`yes/no`)
- Safe cancellation when user inputs `no`

### 3. **Visual Distinction**
- 🟢 Simulation / 🔴 Real Trading emojis
- Clear warning messages
- Detailed result logging

### 4. **Timeout Handling**
- Timeout applied to all async calls
- Default 30 seconds, batch 45 seconds
- Safe termination on network issues

## 📝 Logs and Results

### Log Levels
- **INFO**: General execution information
- **WARNING**: Cautions (buy/sell failures)
- **ERROR**: Error occurrences

### Result Format
```python
{
    'success': True,           # Success status
    'stock_code': '005930',    # Stock code
    'quantity': 8,             # Quantity
    'current_price': 62500,    # Current price
    'total_amount': 500000,    # Total amount
    'message': 'Buy completed...',  # Result message
    'timestamp': '2025-09-07T...'  # Execution time
}
```

## 🐛 Troubleshooting

### Authentication Error
```
AuthenticationError: Authentication failed
```
**Solution**:
- Check `trading/config/kis_devlp.yaml` configuration
- Verify API key and secret key
- Re-authenticate if token expired

### Module Import Error
```
ModuleNotFoundError: No module named 'trading'
```
**Solution**:
- Run from project root: `python tests/quick_test.py`
- Verify path: Check `sys.path` configuration

### Config File Error
```
FileNotFoundError: kis_devlp.yaml
```
**Solution**:
- Verify `trading/config/kis_devlp.yaml` file exists
- Refer to `trading/config/kis_devlp.yaml.example` for configuration

### Out-of-Market-Hours Error
```
Order time not available
```
**Solution**:
- Test during market hours (09:00-15:30)
- Check supported hours for simulation

## 📞 Support

If problems occur:
1. Check logs first
2. Verify configuration files
3. Check network connection status
4. Contact development team if needed

## 🎯 Recommended Usage Patterns

### 1. **Quick Testing During Development**
```bash
python tests/quick_test.py portfolio
```

### 2. **Individual Function Testing**
```bash
python tests/quick_test.py buy
python tests/quick_test.py sell
```

### 3. **Full System Verification**
```bash
python tests/test_async_trading.py
# Select "3. All tests" from menu
```

### 4. **Final Verification Before Production**
```bash
python tests/test_async_trading.py
# Run all tests in simulation first
# Then test with small amounts in real trading
```

---

**⚠️ Final Reminder**: Always start with simulation, and proceed with real trading cautiously after sufficient verification! 🚀
