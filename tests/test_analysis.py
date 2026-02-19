from trading.analysis import MarketDataBuffer

def test_buffer_add():
    buf = MarketDataBuffer(maxlen=5)
    buf.add_price("AAPL", 100)
    buf.add_price("AAPL", 102)
    stats = buf.get_stats("AAPL")
    
    assert stats["current"] == 102
    assert stats["change_pct"] == 2.0
    assert stats["ma5"] == 101.0

def test_buffer_maxlen():
    buf = MarketDataBuffer(maxlen=2)
    buf.add_price("AAPL", 100)
    buf.add_price("AAPL", 101)
    buf.add_price("AAPL", 102)
    
    stats = buf.get_stats("AAPL")
    # Buffer should contain [101, 102]
    assert stats["current"] == 102
    # Change % is (102-101)/101 * 100 = 0.99
    assert abs(stats["change_pct"] - 0.990099) < 0.0001
