#!/usr/bin/env python3
"""
PRISM-INSIGHT Trading Signal Subscriber (GCP Pub/Sub Auto-Trading Integration)

Running this script will receive buy/sell signals published by PRISM-INSIGHT
in real-time via GCP Pub/Sub and execute actual auto-trading.

Usage:
    1. Install google-cloud-pubsub package
       pip install google-cloud-pubsub

    2. Configure .env file (or pass via environment variables/options)
       GCP_PROJECT_ID=your-project-id
       GCP_PUBSUB_SUBSCRIPTION_ID=prism-trading-signals-sub
       GCP_CREDENTIALS_PATH=/path/to/service-account-key.json

    3. Run script
       python examples/messaging/gcp_pubsub_subscriber_example.py

Options:
    --log-file: Specify log file path (default: logs/subscriber_YYYYMMDD.log)
    --dry-run: Run simulation only without actual trading
"""
import os
import sys
import json
import logging
import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# Project root path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env file
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


def setup_logging(log_file: str = None) -> logging.Logger:
    """Configure logging"""
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)

    if log_file:
        log_path = Path(log_file)
    else:
        log_path = log_dir / f"subscriber_{datetime.now().strftime('%Y%m%d')}.log"

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding='utf-8')
        ]
    )

    logger = logging.getLogger("subscriber")
    logger.info(f"Log file: {log_path}")

    return logger


async def execute_buy_trade(ticker: str, company_name: str, logger: logging.Logger) -> Dict[str, Any]:
    """Execute actual buy order (async)"""
    try:
        from trading.domestic_stock_trading import AsyncTradingContext

        async with AsyncTradingContext() as trading:
            trade_result = await trading.async_buy_stock(stock_code=ticker)

        if trade_result['success']:
            logger.info(f"✅ Actual buy successful: {company_name}({ticker}) - {trade_result['message']}")
        else:
            logger.error(f"❌ Actual buy failed: {company_name}({ticker}) - {trade_result['message']}")

        return trade_result

    except ImportError as e:
        logger.error(f"Trading module import failed: {e}")
        return {"success": False, "message": f"Import error: {e}"}
    except Exception as e:
        logger.error(f"Error during buy execution: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


async def execute_sell_trade(ticker: str, company_name: str, logger: logging.Logger) -> Dict[str, Any]:
    """Execute actual sell order (async)"""
    try:
        from trading.domestic_stock_trading import AsyncTradingContext

        async with AsyncTradingContext() as trading:
            trade_result = await trading.async_sell_stock(stock_code=ticker)

        if trade_result['success']:
            logger.info(f"✅ Actual sell successful: {company_name}({ticker}) - {trade_result['message']}")
        else:
            logger.error(f"❌ Actual sell failed: {company_name}({ticker}) - {trade_result['message']}")

        return trade_result

    except ImportError as e:
        logger.error(f"Trading module import failed: {e}")
        return {"success": False, "message": f"Import error: {e}"}
    except Exception as e:
        logger.error(f"Error during sell execution: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


def main():
    parser = argparse.ArgumentParser(description="PRISM-INSIGHT GCP Pub/Sub Trading Signal Subscriber")
    parser.add_argument(
        "--project-id",
        default=os.environ.get("GCP_PROJECT_ID"),
        help="GCP Project ID"
    )
    parser.add_argument(
        "--subscription-id",
        default=os.environ.get("GCP_PUBSUB_SUBSCRIPTION_ID", "prism-trading-signals-sub"),
        help="GCP Pub/Sub Subscription ID"
    )
    parser.add_argument(
        "--credentials-path",
        default=os.environ.get("GCP_CREDENTIALS_PATH"),
        help="Path to GCP service account JSON key file"
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Log file path (default: logs/subscriber_YYYYMMDD.log)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run simulation only without actual trading (default: actual trading)"
    )
    args = parser.parse_args()

    # Configure logging
    logger = setup_logging(args.log_file)

    # Display mode
    if args.dry_run:
        logger.warning("🔸 DRY-RUN mode: No actual trading will be executed.")
    else:
        logger.info("🔹 LIVE mode: Actual trading will be executed!")

    # Check GCP connection info
    if not args.project_id or not args.subscription_id:
        logger.error("GCP connection information is missing.")
        logger.error("Set environment variables or use --project-id, --subscription-id options.")
        logger.error('Example: export GCP_PROJECT_ID="your-project-id"')
        logger.error('         export GCP_PUBSUB_SUBSCRIPTION_ID="prism-trading-signals-sub"')
        return

    try:
        from google.cloud import pubsub_v1
    except ImportError:
        logger.error("google-cloud-pubsub package not installed.")
        logger.error("Install with: pip install google-cloud-pubsub")
        return

    # Set credentials if provided
    if args.credentials_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = args.credentials_path

    # Connect to GCP Pub/Sub
    logger.info("Connecting to GCP Pub/Sub...")
    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(args.project_id, args.subscription_id)

    logger.info(f"Subscription started: {subscription_path}")
    logger.info("=" * 60)

    # Statistics
    message_count = 0
    trade_count = {"BUY": 0, "SELL": 0}

    # Signal handler function
    def handle_signal(signal: dict):
        """Function to process received signals"""
        nonlocal message_count, trade_count

        signal_type = signal.get("type", "UNKNOWN")
        ticker = signal.get("ticker", "")
        company_name = signal.get("company_name", "")
        price = signal.get("price", 0)

        # Emoji by signal type
        emoji = {
            "BUY": "📈",
            "SELL": "📉",
            "EVENT": "🔔"
        }.get(signal_type, "📌")

        # Log basic signal info
        logger.info(f"{emoji} [{signal_type}] {company_name}({ticker}) @ {price:,.0f} KRW")

        # If buy signal
        if signal_type == "BUY":
            target = signal.get("target_price", 0)
            stop_loss = signal.get("stop_loss", 0)
            rationale = signal.get("rationale", "")
            buy_score = signal.get("buy_score", 0)

            details = []
            if target:
                details.append(f"Target: {target:,.0f} KRW")
            if stop_loss:
                details.append(f"Stop-loss: {stop_loss:,.0f} KRW")
            if buy_score:
                details.append(f"Buy score: {buy_score}")
            if rationale:
                details.append(f"Rationale: {rationale[:100]}...")

            if details:
                logger.info(f"   -> {' | '.join(details)}")

            # Execute actual buy
            if not args.dry_run:
                logger.info(f"🚀 Executing buy order: {company_name}({ticker})")
                asyncio.run(execute_buy_trade(ticker, company_name, logger))
            else:
                logger.info(f"🔸 [DRY-RUN] Buy skipped: {company_name}({ticker})")

            trade_count["BUY"] += 1

        # If sell signal
        elif signal_type == "SELL":
            profit_rate = signal.get("profit_rate", 0)
            sell_reason = signal.get("sell_reason", "")
            buy_price = signal.get("buy_price", 0)

            details = []
            if buy_price:
                details.append(f"Buy price: {buy_price:,.0f} KRW")
            details.append(f"Profit rate: {profit_rate:+.2f}%")
            if sell_reason:
                details.append(f"Sell reason: {sell_reason}")

            logger.info(f"   -> {' | '.join(details)}")

            # Execute actual sell
            if not args.dry_run:
                logger.info(f"🚀 Executing sell order: {company_name}({ticker})")
                asyncio.run(execute_sell_trade(ticker, company_name, logger))
            else:
                logger.info(f"🔸 [DRY-RUN] Sell skipped: {company_name}({ticker})")

            trade_count["SELL"] += 1

        # If event signal
        elif signal_type == "EVENT":
            event_type = signal.get("event_type", "")
            event_source = signal.get("source", "")
            event_description = signal.get("event_description", "")

            details = []
            if event_type:
                details.append(f"Event: {event_type}")
            if event_source:
                details.append(f"Source: {event_source}")
            if event_description:
                details.append(f"Description: {event_description[:100]}")

            if details:
                logger.info(f"   -> {' | '.join(details)}")

        message_count += 1
        logger.debug(f"Original signal: {json.dumps(signal, ensure_ascii=False)}")

    # Callback function
    def callback(message):
        """GCP Pub/Sub message callback"""
        try:
            signal = json.loads(message.data.decode("utf-8"))
            handle_signal(signal)
            message.ack()
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            message.nack()

    # Subscribe
    streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)
    logger.info(f"Listening for messages on {subscription_path}...")

    # Main loop
    try:
        streaming_pull_future.result()
    except KeyboardInterrupt:
        streaming_pull_future.cancel()
        logger.info("=" * 60)
        logger.info(f"Subscription ended.")
        logger.info(f"Total {message_count} signals received (Buy: {trade_count['BUY']}, Sell: {trade_count['SELL']})")


if __name__ == "__main__":
    main()
