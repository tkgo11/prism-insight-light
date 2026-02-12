"""
GCP Pub/Sub subscriber that listens for trading signals and executes trades.

Usage:
    python gcp_pubsub_subscriber_example.py                  # dry-run
    python gcp_pubsub_subscriber_example.py --execute        # live trades
    python gcp_pubsub_subscriber_example.py --execute --mode demo  # demo mode
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
from typing import Dict, Optional

from dotenv import load_dotenv
from google.cloud import pubsub_v1
from google.oauth2 import service_account

from trading.domestic_stock_trading import DomesticStockTrading
from trading.us_stock_trading import USStockTrading

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scheduled Order Manager (for off-hours demo mode)
# ---------------------------------------------------------------------------

class ScheduledOrderManager:
    """Manages orders scheduled for execution during market hours."""

    def __init__(self, save_path: str = "scheduled_orders.json"):
        self.save_path = save_path
        self.orders = []
        self._scheduler_thread = None
        self._running = False
        self.load_orders()

    def load_orders(self):
        try:
            if Path(self.save_path).exists():
                self.orders = json.loads(Path(self.save_path).read_text(encoding="utf-8"))
                logger.info(f"Loaded {len(self.orders)} scheduled orders")
        except Exception:
            self.orders = []

    def save_orders(self):
        try:
            Path(self.save_path).write_text(json.dumps(self.orders, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to save orders: {e}")

    def add_order(self, signal: Dict, signal_type: str = "BUY", market: str = "KR") -> bool:
        order = {
            "signal": signal,
            "signal_type": signal_type,
            "market": market,
            "status": "pending",
            "created_at": datetime.datetime.now().isoformat(),
        }
        self.orders.append(order)
        self.save_orders()
        logger.info(f"Scheduled {signal_type} order for {signal.get('ticker', '')}")
        return True

    def check_and_execute(self, execute_callback):
        """Execute pending orders if market is open."""
        if not is_market_hours("KR"):
            return
        pending = [o for o in self.orders if o["status"] == "pending"]
        for order in pending:
            try:
                execute_callback(order["signal"], order["signal_type"])
                order["status"] = "executed"
                order["executed_at"] = datetime.datetime.now().isoformat()
            except Exception as e:
                order["status"] = "failed"
                order["error"] = str(e)
        self.save_orders()

    def start_scheduler(self, execute_callback):
        """Start background thread to check and execute scheduled orders."""
        self._running = True

        def _loop():
            while self._running:
                self.check_and_execute(execute_callback)
                time.sleep(60)

        self._scheduler_thread = threading.Thread(target=_loop, daemon=True)
        self._scheduler_thread.start()
        logger.info("Scheduler started")

    def stop_scheduler(self):
        self._running = False
        logger.info("Scheduler stopped")


# ---------------------------------------------------------------------------
# Market hours check
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Trade execution functions
# ---------------------------------------------------------------------------

async def execute_buy_trade(ticker: str, company_name: str, trade_logger: logging.Logger,
                            limit_price: Optional[int] = None) -> Dict:
    """Execute a KR buy trade."""
    trader = DomesticStockTrading()
    result = await trader.async_buy_stock(ticker, limit_price=limit_price)
    trade_logger.info(f"Buy result for {company_name}({ticker}): {result.get('message', '')}")
    return result


async def execute_sell_trade(ticker: str, company_name: str, trade_logger: logging.Logger,
                             limit_price: Optional[int] = None) -> Dict:
    """Execute a KR sell trade."""
    trader = DomesticStockTrading()
    result = await trader.async_sell_stock(ticker, limit_price=limit_price)
    trade_logger.info(f"Sell result for {company_name}({ticker}): {result.get('message', '')}")
    return result


async def execute_us_buy_trade(ticker: str, company_name: str, trade_logger: logging.Logger,
                               limit_price: Optional[float] = None) -> Dict:
    """Execute a US buy trade."""
    # Note: limit_price is ignored here as simplified buy_market calculates its own buffer
    # Or pass it if you want strict limit support.
    trader = USStockTrading()
    # For US, amount/price logic might differ. Here we just call async_buy
    result = await trader.async_buy(ticker)
    trade_logger.info(f"US Buy result for {company_name}({ticker}): {result.get('message', '')}")
    return result


async def execute_us_sell_trade(ticker: str, company_name: str, trade_logger: logging.Logger,
                                limit_price: Optional[float] = None) -> Dict:
    """Execute a US sell trade."""
    trader = USStockTrading()
    result = await trader.async_sell(ticker)
    trade_logger.info(f"US Sell result for {company_name}({ticker}): {result.get('message', '')}")
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Subscribe to trading signals")
    parser.add_argument("--execute", action="store_true", help="Execute trades (default: dry-run)")
    parser.add_argument("--mode", default="demo", choices=["demo", "real"], help="Trading mode")
    args = parser.parse_args()

    dry_run = not args.execute

    # Setup GCP subscriber
    project_id = os.getenv("GCP_PROJECT_ID")
    subscription_id = os.getenv("GCP_PUBSUB_SUBSCRIPTION_ID")
    creds_path = os.getenv("GCP_CREDENTIALS_PATH")

    if not all([project_id, subscription_id]):
        logger.error("Set GCP_PROJECT_ID and GCP_PUBSUB_SUBSCRIPTION_ID in .env")
        sys.exit(1)

    if creds_path:
        creds = service_account.Credentials.from_service_account_file(creds_path)
        subscriber = pubsub_v1.SubscriberClient(credentials=creds)
    else:
        subscriber = pubsub_v1.SubscriberClient()

    subscription_path = subscriber.subscription_path(project_id, subscription_id)

    # Scheduled order manager for demo mode
    scheduled_order_manager = None
    if args.mode == "demo" and not dry_run:
        scheduled_order_manager = ScheduledOrderManager()

    message_count = 0
    trade_count = {"BUY": 0, "SELL": 0}

    mode_label = "LIVE" if not dry_run else "DRY-RUN"
    logger.info(f"Starting subscriber ({mode_label}, mode={args.mode}): {subscription_path}")

    def handle_signal(signal: dict):
        nonlocal message_count
        signal_type = signal.get("signal_type", "").upper()
        ticker = signal.get("ticker", "")
        company = signal.get("company_name", "")
        price = signal.get("price")
        market = signal.get("market", "KR").upper()
        market_label = f"[{market}]"

        logger.info(f"📨 Signal: {signal_type} {market_label} {company}({ticker}) @ {price}")

        if signal_type == "BUY":
            if not dry_run:
                if not is_market_hours(market) and scheduled_order_manager:
                    scheduled_order_manager.add_order(signal, "BUY", market)
                    logger.info(f"📅 Scheduled BUY for next market open")
                else:
                    if market == "US":
                        asyncio.run(execute_us_buy_trade(ticker, company, logger,
                                                         limit_price=float(price) if price else None))
                    else:
                        asyncio.run(execute_buy_trade(ticker, company, logger,
                                                      limit_price=int(price) if price else None))
            else:
                logger.info(f"🔸 [DRY-RUN] Buy skipped")
            trade_count["BUY"] += 1

        elif signal_type == "SELL":
            if not dry_run:
                if not is_market_hours(market) and scheduled_order_manager:
                    scheduled_order_manager.add_order(signal, "SELL", market)
                    logger.info(f"📅 Scheduled SELL for next market open")
                else:
                    if market == "US":
                        asyncio.run(execute_us_sell_trade(ticker, company, logger,
                                                          limit_price=float(price) if price else None))
                    else:
                        asyncio.run(execute_sell_trade(ticker, company, logger,
                                                       limit_price=int(price) if price else None))
            else:
                logger.info(f"🔸 [DRY-RUN] Sell skipped")
            trade_count["SELL"] += 1

        elif signal_type == "EVENT":
            details = []
            if signal.get("event_type"):
                details.append(f"Event: {signal['event_type']}")
            if signal.get("source"):
                details.append(f"Source: {signal['source']}")
            if signal.get("event_description"):
                details.append(f"Desc: {signal['event_description'][:100]}")
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

    # Start scheduler if in demo mode
    if scheduled_order_manager:
        def execute_scheduled(sig, sig_type):
            ticker = sig.get("ticker", "")
            company = sig.get("company_name", "")
            price = sig.get("price")
            if sig_type == "BUY":
                asyncio.run(execute_buy_trade(ticker, company, logger, limit_price=int(price) if price else None))
            elif sig_type == "SELL":
                asyncio.run(execute_sell_trade(ticker, company, logger, limit_price=int(price) if price else None))

        scheduled_order_manager.start_scheduler(execute_scheduled)

    # Subscribe
    streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)
    logger.info(f"Listening for messages on {subscription_path}...")

    try:
        streaming_pull_future.result()
    except KeyboardInterrupt:
        streaming_pull_future.cancel()
        if scheduled_order_manager:
            scheduled_order_manager.stop_scheduler()
            pending = len([o for o in scheduled_order_manager.orders if o["status"] == "pending"])
            if pending > 0:
                logger.info(f"📋 {pending} scheduled orders pending for next run")
        logger.info(f"Ended. {message_count} signals (Buy: {trade_count['BUY']}, Sell: {trade_count['SELL']})")


if __name__ == "__main__":
    main()
