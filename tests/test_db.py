import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from trading.database import Base, ScheduledOrder, TradeLog
from datetime import datetime

# Use in-memory SQLite for testing
SQLALCHEMY_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@pytest.fixture
def db():
    Base.metadata.create_all(bind=engine)
    session = TestingSessionLocal()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)

def test_create_scheduled_order(db):
    order = ScheduledOrder(
        ticker="AAPL",
        signal_type="BUY",
        market="US",
        price=150.0,
        execute_after=datetime.now()
    )
    db.add(order)
    db.commit()
    
    saved_order = db.query(ScheduledOrder).first()
    assert saved_order.ticker == "AAPL"
    assert saved_order.status == "pending"

def test_trade_log(db):
    log = TradeLog(
        ticker="005930",
        action="BUY",
        quantity=10,
        price=70000,
        total_amount=700000,
        success=True,
        message="Test executed"
    )
    db.add(log)
    db.commit()
    
    saved_log = db.query(TradeLog).first()
    assert saved_log.ticker == "005930"
    assert saved_log.total_amount == 700000
