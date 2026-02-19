from pydantic import BaseModel, Field, field_validator
from typing import Optional, Literal
from datetime import datetime

class TradeSignal(BaseModel):
    """
    Schema for incoming trading signals from Pub/Sub.
    """
    ticker: str = Field(..., description="Stock symbol (e.g., AAPL, 005930)")
    company_name: str = Field("", description="Company name")
    signal_type: Literal["BUY", "SELL", "EVENT"] = Field(..., description="Type of signal")
    price: Optional[float] = Field(None, description="Signal price")
    market: Literal["KR", "US"] = Field("KR", description="Market identifier")
    timestamp: Optional[datetime] = Field(default_factory=datetime.now)
    source: Optional[str] = Field(None, description="Signal source")
    
    @field_validator('ticker')
    @classmethod
    def ticker_must_be_upper(cls, v: str) -> str:
        return v.upper()

class EventSignal(TradeSignal):
    """Extended schema for EVENT type signals."""
    event_type: Optional[str] = None
    event_description: Optional[str] = None
