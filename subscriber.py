"""
GCP Pub/Sub subscriber that listens for trading signals and executes trades.

Usage:
    python subscriber.py                  # Live trading (based on config)
    python subscriber.py --dry-run        # Simulation mode
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
import time
import datetime
from pathlib import Path
from typing import Dict, Optional, List, Any

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
from google.cloud import pubsub_v1
from google.oauth2 import service_account
import yaml

from trading.domestic_stock_trading import DomesticStockTrading
from trading.us_stock_trading import AsyncUSTradingContext
from trading.schemas import TradeSignal
from trading.database import init_db, SessionLocal, ScheduledOrder, TradeLog
from trading.notifier import NotifierManager, SlackNotifier, DiscordNotifier
from trading.analysis import MarketDataBuffer

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

def get_trading_mode() -> str:
    """Check trading mode (demo/real) from kis_devlp.yaml"""
    try:
        config_path = PROJECT_ROOT / "trading" / "config" / "kis_devlp.yaml"
        # If config file exists in the new location, use it. Otherwise try the old location relative to script.
        if not config_path.exists():
             config_path = PROJECT_ROOT / "trading" / "kis_devlp.yaml"
             
        if config_path.exists():
            with open(config_path, encoding="UTF-8") as f:
                cfg = yaml.load(f, Loader=yaml.FullLoader)
            return cfg.get("default_mode", "real")
        return "real"
    except Exception as e:
        logger.warning(f"Failed to load config, defaulting to real mode: {e}")
        return "real"


def is_market_hours(market: str = "KR") -> bool:
    """Check if the market is currently open."""
    now = datetime.datetime.now()
    if now.weekday() >= 5:  # Weekend
        return False
    t = now.time()
    if market == "KR":
        return datetime.time(9, 0) <= t <= datetime.time(15, 30)
    elif market == "US":
        # US market hours in KST (roughly 23:30 ~ 06:00 next day)
        return t >= datetime.time(23, 30) or t <= datetime.time(6, 0)
    return False


def get_next_market_open() -> datetime.datetime:
    """Calculate next market open time (09:05 on next business day)."""
    now = datetime.datetime.now()
    next_day = now + datetime.timedelta(days=1)

    # Find next business day (search up to 7 days)
    for _ in range(7):
        # Skip weekends
        if next_day.weekday() >= 5:
            next_day += datetime.timedelta(days=1)
            continue
        
        # Simple holiday check could go here if needed
        break

    # Set to 09:05
    return next_day.replace(hour=9, minute=5, second=0, microsecond=0)


# ---------------------------------------------------------------------------
# Scheduled Order Manager (for demo mode off-hours)
# ---------------------------------------------------------------------------

class ScheduledOrderManager:
    """Manages orders scheduled for execution during market hours (SQLite version)."""
    
    def __init__(self):
        init_db()
        self._scheduler_thread = None
        self._running = False
        
    def add_order(self, signal: Dict, signal_type: str = "BUY", market: str = "KR") -> bool:
        session = SessionLocal()
        try:
            ticker = signal.get("ticker", "")
            execute_after = get_next_market_open()
            
            new_order = ScheduledOrder(
                ticker=ticker,
                company_name=signal.get("company_name"),
                signal_type=signal_type,
                market=market,
                price=signal.get("price"),
                execute_after=execute_after,
                status="pending",
                signal_data=signal  # Store full JSON payload for reconstruction
            )
            session.add(new_order)
            session.commit()
            
            logger.info(f"📅 Scheduled {signal_type} for {ticker} at {execute_after}")
            return True
        except Exception as e:
            logger.error(f"Failed to add scheduled order: {e}")
            return False
        finally:
            session.close()

    def check_and_execute(self, execute_callback):
        """Execute pending orders if market is open."""
        # Check market hours first per market type? Or iterate all?
        # Since mixed markets, we iterate pending orders and check condition per order.
        
        session = SessionLocal()
        try:
            now = datetime.datetime.now()
            
            # Fetch pending orders that are ready to execute
            pending_orders = session.query(ScheduledOrder).filter(
                ScheduledOrder.status == "pending",
                ScheduledOrder.execute_after <= now
            ).all()
            
            if not pending_orders:
                return

            for order in pending_orders:
                # Double check market open status for specific market
                if not is_market_hours(order.market):
                    continue

                try:
                    logger.info(f"🚀 Executing scheduled order #{order.id}: {order.ticker}")
                    # Reconstruct signal dict or Pydantic model?
                    # Callback expects dict probably, or however established. 
                    # Previous code: execute_callback(order["signal"], order["signal_type"])
                    
                    signal_payload = order.signal_data
                    if signal_payload:
                        execute_callback(signal_payload, order.signal_type)
                    
                        order.status = "executed"
                        order.executed_at = datetime.datetime.now()
                        session.commit() # Commit success
                    else:
                        order.status = "failed"
                        order.error_message = "Missing signal data"
                        session.commit()

                except Exception as e:
                    order.status = "failed"
                    order.error_message = str(e)
                    session.commit()
                    logger.error(f"Scheduled execution failed for #{order.id}: {e}")
                    
        except Exception as e:
            logger.error(f"Scheduler check error: {e}")
        finally:
            session.close()

    def start_scheduler(self, execute_callback):
        """Start background thread to check and execute scheduled orders."""
        self._running = True
        
        def _loop():
            logger.info("🕐 Scheduler started (DB)")
            while self._running:
                try:
                    self.check_and_execute(execute_callback)
                except Exception as e:
                    logger.error(f"Scheduler error: {e}")
                time.sleep(60) # Check every minute

        self._scheduler_thread = threading.Thread(target=_loop, daemon=True)
        self._scheduler_thread.start()

    def stop_scheduler(self):
        self._running = False
        logger.info("Scheduler stopped")


# ---------------------------------------------------------------------------
# Trade execution functions
# ---------------------------------------------------------------------------

async def execute_buy_trade(ticker: str, company_name: str, trade_logger: logging.Logger,
                            limit_price: Optional[int] = None) -> Dict:
    """Execute a KR buy trade."""
    trader = DomesticStockTrading()
    result = await trader.async_buy_stock(ticker, limit_price=limit_price)
    trade_logger.info(f"✅ Buy executed for {company_name}({ticker}): {result.get('message', '')}")
    return result


async def execute_sell_trade(ticker: str, company_name: str, trade_logger: logging.Logger,
                             limit_price: Optional[int] = None) -> Dict:
    """Execute a KR sell trade."""
    trader = DomesticStockTrading()
    result = await trader.async_sell_stock(ticker, limit_price=limit_price)
    trade_logger.info(f"✅ Sell executed for {company_name}({ticker}): {result.get('message', '')}")
    return result


async def execute_us_buy_trade(ticker: str, company_name: str, trade_logger: logging.Logger,
                               limit_price: Optional[float] = None) -> Dict:
    """Execute a US buy trade."""
    async with AsyncUSTradingContext() as trader:
        result = await trader.async_buy_stock(ticker, limit_price=limit_price)
    
    trade_logger.info(f"✅ US Buy executed for {company_name}({ticker}): {result.get('message', '')}")
    return result


async def execute_us_sell_trade(ticker: str, company_name: str, trade_logger: logging.Logger,
                                limit_price: Optional[float] = None) -> Dict:
    """Execute a US sell trade."""
    async with AsyncUSTradingContext() as trader:
        result = await trader.async_sell_stock(ticker, limit_price=limit_price)

    trade_logger.info(f"✅ US Sell executed for {company_name}({ticker}): {result.get('message', '')}")
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="PRISM-INSIGHT Trading Signal Subscriber")
    parser.add_argument("--dry-run", action="store_true", help="Run simulation only without actual trading")
    parser.add_argument("--project-id", default=os.getenv("GCP_PROJECT_ID"), help="GCP Project ID")
    parser.add_argument("--subscription-id", default=os.getenv("GCP_PUBSUB_SUBSCRIPTION_ID"), help="GCP Subscription ID")
    args = parser.parse_args()

    # Determine execution mode
    dry_run = args.dry_run
    trading_mode = get_trading_mode()
    
    # Initialize DB (for logging and scheduling)
    init_db()

    # Log startup status
    if dry_run:
        logger.warning("🔸 DRY-RUN mode: No actual trading will be executed.")
    else:
        logger.info("🔹 LIVE mode: Actual trading will be executed!")
        logger.info(f"🔹 Trading mode: {trading_mode.upper()}")

    # Setup GCP subscriber
    project_id = args.project_id
    subscription_id = args.subscription_id
    creds_path = os.getenv("GCP_CREDENTIALS_PATH")

    if not all([project_id, subscription_id]):
        logger.error("Set GCP_PROJECT_ID and GCP_PUBSUB_SUBSCRIPTION_ID in .env or arguments")
        sys.exit(1)

    if creds_path:
        creds = service_account.Credentials.from_service_account_file(creds_path)
        subscriber = pubsub_v1.SubscriberClient(credentials=creds)
    else:
        subscriber = pubsub_v1.SubscriberClient()

    subscription_path = subscriber.subscription_path(project_id, subscription_id)

    # Scheduled order manager (Only active in DEMO mode + LIVE execution)
    scheduled_order_manager = None
    if not dry_run and trading_mode == "demo":
        scheduled_order_manager = ScheduledOrderManager()
        logger.info("📅 Demo mode scheduler enabled for off-hours trading")

    message_count = 0
    trade_count = {"BUY": 0, "SELL": 0}

    logger.info(f"Starting subscriber on: {subscription_path}")

    # Market Data Buffer
    market_buffer = MarketDataBuffer(maxlen=50)

    # Notifier
    notifier = NotifierManager(
        slack_webhook=os.getenv("SLACK_WEBHOOK_URL"),
        discord_webhook=os.getenv("DISCORD_WEBHOOK_URL")
    )

    def log_trade(signal_type, ticker, price, quantity, success, msg):
        """Log trade to DB."""
        session = SessionLocal()
        try:
            log = TradeLog(
                ticker=ticker,
                action=signal_type,
                quantity=quantity,
                price=float(price or 0),
                total_amount=quantity * float(price or 0),
                success=success,
                message=msg
            )
            session.add(log)
            session.commit()
        except Exception as e:
            logger.error(f"Failed to log trade: {e}")
        finally:
            session.close()

    def handle_signal(signal_data: dict):
        nonlocal message_count
        try:
            # 1. Pydantic Validation
            signal = TradeSignal(**signal_data)
        except Exception as e:
            logger.error(f"⚠️ Signal validation failed: {e}")
            return

        signal_type = signal.signal_type
        ticker = signal.ticker
        company = signal.company_name
        # Update market buffer
        if price:
            try:
                p_val = float(price)
                market_buffer.add_price(ticker, p_val)
                stats = market_buffer.get_stats(ticker)
                if stats and abs(stats["change_pct"]) > 2.0:
                    logger.info(f"📊 {ticker} Volatility Alert: {stats['change_pct']:.2f}% change (Last: {p_val}, MA5: {stats['ma5']:.2f})")
            except ValueError:
                pass
        
        # Determine emoji based on signal type
        emoji = {"BUY": "📈", "SELL": "📉", "EVENT": "🔔"}.get(signal_type, "📌")
        
        logger.info(f"{emoji} Signal: {signal_type} [{market}] {company}({ticker}) @ {price}")

        if signal_type == "BUY":
            if not dry_run:
                if trading_mode == "demo" and not is_market_hours(market) and scheduled_order_manager:
                    scheduled_order_manager.add_order(signal.model_dump(), "BUY", market)
                else:
                    logger.info(f"🚀 Executing BUY: {company}({ticker})")
                    if market == "US":
                        asyncio.run(execute_us_buy_trade(ticker, company, logger,
                                                         limit_price=float(price) if price else None))
                        log_trade("BUY", ticker, price, 1, True, "Executed") # Simplified quantity logging for now
                        notifier.send(f"BUY Execution: {company} ({ticker}) @ {price}", color="green")
                    else:
                        asyncio.run(execute_buy_trade(ticker, company, logger,
                                                      limit_price=int(price) if price else None))
                        log_trade("BUY", ticker, price, 1, True, "Executed")
                        notifier.send(f"BUY Execution: {company} ({ticker}) @ {price}", color="green")
            else:
                logger.info(f"🔸 [DRY-RUN] Buy skipped")
            trade_count["BUY"] += 1

        elif signal_type == "SELL":
            if not dry_run:
                if trading_mode == "demo" and not is_market_hours(market) and scheduled_order_manager:
                    scheduled_order_manager.add_order(signal.model_dump(), "SELL", market)
                else:
                    logger.info(f"🚀 Executing SELL: {company}({ticker})")
                    if market == "US":
                        asyncio.run(execute_us_sell_trade(ticker, company, logger,
                                                          limit_price=float(price) if price else None))
                        log_trade("SELL", ticker, price, 1, True, "Executed")
                        notifier.send(f"SELL Execution: {company} ({ticker}) @ {price}", color="red")
                    else:
                        asyncio.run(execute_sell_trade(ticker, company, logger,
                                                       limit_price=int(price) if price else None))
                        log_trade("SELL", ticker, price, 1, True, "Executed")
                        notifier.send(f"SELL Execution: {company} ({ticker}) @ {price}", color="red")
            else:
                logger.info(f"🔸 [DRY-RUN] Sell skipped")
            trade_count["SELL"] += 1

        elif signal_type == "EVENT":
            details = []
            if signal.source: details.append(f"Source: {signal.source}")
            if details:
                logger.info(f"   -> {' | '.join(details)}")

        message_count += 1

    def callback(message):
        try:
            signal = json.loads(message.data.decode("utf-8"))
            handle_signal(signal)
            message.ack()
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            message.nack()

    # Start scheduler callback if in demo mode
    if scheduled_order_manager:
        def execute_scheduled(sig, sig_type):
            ticker = sig.get("ticker", "")
            company = sig.get("company_name", "")
            price = sig.get("price")
            market = sig.get("market", "KR").upper()
            
            if market == "US":
                limit_price = float(price) if price else None
                if sig_type == "BUY":
                    asyncio.run(execute_us_buy_trade(ticker, company, logger, limit_price=limit_price))
                elif sig_type == "SELL":
                    asyncio.run(execute_us_sell_trade(ticker, company, logger, limit_price=limit_price))
            else:
                limit_price = int(price) if price else None
                if sig_type == "BUY":
                    asyncio.run(execute_buy_trade(ticker, company, logger, limit_price=limit_price))
                elif sig_type == "SELL":
                    asyncio.run(execute_sell_trade(ticker, company, logger, limit_price=limit_price))

        scheduled_order_manager.start_scheduler(execute_scheduled)

    # Subscribe
    streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)
    logger.info(f"Listening for messages...")

    try:
        streaming_pull_future.result()
    except KeyboardInterrupt:
        streaming_pull_future.cancel()
        if scheduled_order_manager:
            scheduled_order_manager.stop_scheduler()
            # pending_count manual check if needed, or query db
            session = SessionLocal()
            pending = session.query(ScheduledOrder).filter(ScheduledOrder.status == "pending").count()
            session.close()
            if pending > 0:
                logger.info(f"📋 {pending} scheduled orders pending for next run")
        logger.info(f"Ended. {message_count} signals (Buy: {trade_count['BUY']}, Sell: {trade_count['SELL']})")


if __name__ == "__main__":
    main()
