import datetime
from typing import List, Optional
from sqlalchemy import create_engine, Column, Integer, String, Float, DateTime, Boolean, JSON
from sqlalchemy.orm import declarative_base, sessionmaker, Session
from sqlalchemy.sql import func

Base = declarative_base()

class ScheduledOrder(Base):
    """
    Represents an order scheduled for execution when the market opens.
    """
    __tablename__ = 'scheduled_orders'

    id = Column(Integer, primary_key=True)
    ticker = Column(String, nullable=False)
    company_name = Column(String)
    signal_type = Column(String, nullable=False) # BUY, SELL
    market = Column(String, default="KR")
    price = Column(Float, nullable=True)
    status = Column(String, default="pending") # pending, executed, failed
    created_at = Column(DateTime, default=datetime.datetime.now)
    execute_after = Column(DateTime, nullable=False)
    executed_at = Column(DateTime, nullable=True)
    error_message = Column(String, nullable=True)
    signal_data = Column(JSON, nullable=True) # Full original signal payload

    def __repr__(self):
        return f"<ScheduledOrder(id={self.id}, ticker='{self.ticker}', status='{self.status}')>"

class TradeLog(Base):
    """
    Log of executed trades for reporting/dashboard.
    """
    __tablename__ = 'trade_logs'

    id = Column(Integer, primary_key=True)
    ticker = Column(String, nullable=False)
    action = Column(String, nullable=False) # BUY, SELL
    quantity = Column(Integer, nullable=False)
    price = Column(Float, nullable=False)
    total_amount = Column(Float, nullable=False)
    market = Column(String, default="KR")
    timestamp = Column(DateTime, default=datetime.datetime.now)
    order_no = Column(String, nullable=True)
    success = Column(Boolean, default=True)
    message = Column(String, nullable=True)

    def __repr__(self):
        return f"<TradeLog(id={self.id}, ticker='{self.ticker}', action='{self.action}')>"

# Database setup
DB_URL = "sqlite:///trading.db"
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
