import logging
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pathlib import Path

from trading.database import SessionLocal, ScheduledOrder, TradeLog

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("dashboard")

app = FastAPI(title="PRISM-INSIGHT Dashboard")

# Mount static files
static_path = Path(__file__).parent / "static"
static_path.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

templates = Jinja2Templates(directory=str(static_path))

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Render the main dashboard page."""
    db = SessionLocal()
    try:
        # Fetch stats
        scheduled_count = db.query(ScheduledOrder).filter(ScheduledOrder.status == "pending").count()
        todays_trades = db.query(TradeLog).order_by(TradeLog.timestamp.desc()).limit(10).all()
        
        # We can also fetch active holdings if we had a persistent table for them
        # For now, we'll just show what we have in DB
        
        return templates.TemplateResponse("index.html", {
            "request": request,
            "scheduled_count": scheduled_count,
            "recent_trades": todays_trades,
            "system_status": "ONLINE"
        })
    finally:
        db.close()

@app.get("/api/orders")
def get_orders():
    db = SessionLocal()
    orders = db.query(ScheduledOrder).order_by(ScheduledOrder.created_at.desc()).limit(50).all()
    db.close()
    return orders

@app.get("/api/logs")
def get_logs():
    db = SessionLocal()
    logs = db.query(TradeLog).order_by(TradeLog.timestamp.desc()).limit(50).all()
    db.close()
    return logs

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
