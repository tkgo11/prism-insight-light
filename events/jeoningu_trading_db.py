"""
Jeon Ingu Contrarian Trading - SQLite Database Schema (Simplified)

Single table design for event-specific trading simulation.
Stores video analysis and trade history in one unified table.
"""

import aiosqlite
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
import json
import logging

logger = logging.getLogger(__name__)

# Database file location
DB_FILE = Path(__file__).parent / "jeoningu_trading.db"


class JeoninguTradingDB:
    """Database manager for Jeon Ingu contrarian trading simulation"""

    def __init__(self, db_path: str = str(DB_FILE)):
        self.db_path = db_path

    async def initialize(self):
        """Initialize database table"""
        async with aiosqlite.connect(self.db_path) as db:
            # Single unified table for all trading history
            await db.execute("""
                CREATE TABLE IF NOT EXISTS jeoningu_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,

                    -- Video information
                    video_id TEXT NOT NULL,
                    video_title TEXT NOT NULL,
                    video_date TEXT NOT NULL,
                    video_url TEXT NOT NULL,
                    analyzed_date TEXT NOT NULL,

                    -- Analysis results
                    jeon_sentiment TEXT NOT NULL,
                    jeon_reasoning TEXT,
                    contrarian_action TEXT NOT NULL,

                    -- Trade execution
                    trade_type TEXT,
                    stock_code TEXT,
                    stock_name TEXT,
                    quantity INTEGER DEFAULT 0,
                    price REAL DEFAULT 0,
                    amount REAL DEFAULT 0,

                    -- Performance tracking
                    profit_loss REAL DEFAULT 0,
                    profit_loss_pct REAL DEFAULT 0,
                    cumulative_balance REAL NOT NULL,
                    cumulative_return_pct REAL DEFAULT 0,

                    -- Metadata
                    notes TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create index for faster queries
            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_video_id
                ON jeoningu_trades(video_id)
            """)

            await db.execute("""
                CREATE INDEX IF NOT EXISTS idx_trade_date
                ON jeoningu_trades(analyzed_date)
            """)

            await db.commit()
            logger.info(f"Database initialized at {self.db_path}")

    async def insert_trade(self, trade_data: Dict[str, Any]) -> int:
        """
        Insert trade record

        Args:
            trade_data: Dictionary containing trade information

        Returns:
            Inserted row ID
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO jeoningu_trades (
                    video_id, video_title, video_date, video_url, analyzed_date,
                    jeon_sentiment, jeon_reasoning, contrarian_action,
                    trade_type, stock_code, stock_name, quantity, price, amount,
                    profit_loss, profit_loss_pct, cumulative_balance, cumulative_return_pct,
                    notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data['video_id'],
                trade_data['video_title'],
                trade_data['video_date'],
                trade_data['video_url'],
                trade_data['analyzed_date'],
                trade_data['jeon_sentiment'],
                trade_data.get('jeon_reasoning', ''),
                trade_data['contrarian_action'],
                trade_data.get('trade_type'),
                trade_data.get('stock_code'),
                trade_data.get('stock_name'),
                trade_data.get('quantity', 0),
                trade_data.get('price', 0),
                trade_data.get('amount', 0),
                trade_data.get('profit_loss', 0),
                trade_data.get('profit_loss_pct', 0),
                trade_data['cumulative_balance'],
                trade_data.get('cumulative_return_pct', 0),
                trade_data.get('notes', '')
            ))
            await db.commit()
            trade_id = cursor.lastrowid
            logger.info(f"Trade inserted: ID {trade_id}, Action {trade_data['contrarian_action']}")
            return trade_id

    async def get_latest_balance(self) -> float:
        """
        Get latest cumulative balance

        Returns:
            Latest balance, or 0 if no records
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT cumulative_balance
                FROM jeoningu_trades
                ORDER BY id DESC
                LIMIT 1
            """) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0.0

    async def get_current_position(self) -> Optional[Dict[str, Any]]:
        """
        Get current holding position (if any)

        Returns:
            Dictionary with position info, or None if no position
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Find last BUY that hasn't been SELL yet
            # Strategy: only 1 position at a time, so last BUY is current position
            async with db.execute("""
                SELECT * FROM jeoningu_trades
                WHERE trade_type = 'BUY'
                ORDER BY id DESC
                LIMIT 1
            """) as cursor:
                last_buy = await cursor.fetchone()

            if not last_buy:
                return None

            # Check if there's a SELL after this BUY
            async with db.execute("""
                SELECT COUNT(*) FROM jeoningu_trades
                WHERE trade_type = 'SELL' AND id > ?
            """, (last_buy['id'],)) as cursor:
                sell_count = (await cursor.fetchone())[0]

            if sell_count > 0:
                # Position was already sold
                return None

            # Return current position
            return {
                'stock_code': last_buy['stock_code'],
                'stock_name': last_buy['stock_name'],
                'quantity': last_buy['quantity'],
                'buy_price': last_buy['price'],
                'buy_amount': last_buy['amount'],
                'buy_date': last_buy['analyzed_date'],
                'buy_id': last_buy['id']
            }

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get trade history

        Args:
            limit: Maximum number of records to return

        Returns:
            List of trade dictionaries
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM jeoningu_trades
                ORDER BY id DESC
                LIMIT ?
            """, (limit,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def calculate_performance_metrics(self) -> Dict[str, Any]:
        """
        Calculate overall performance metrics

        Returns:
            Dictionary with performance metrics
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Get all SELL trades for win/loss calculation
            async with db.execute("""
                SELECT profit_loss, profit_loss_pct
                FROM jeoningu_trades
                WHERE trade_type = 'SELL'
                ORDER BY id
            """) as cursor:
                sell_trades = await cursor.fetchall()

            if not sell_trades:
                return {
                    "total_trades": 0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "win_rate": 0.0,
                    "cumulative_return": 0.0,
                    "avg_return_per_trade": 0.0
                }

            total_trades = len(sell_trades)
            winning_trades = sum(1 for t in sell_trades if t['profit_loss'] > 0)
            losing_trades = sum(1 for t in sell_trades if t['profit_loss'] <= 0)

            # Get latest cumulative return
            async with db.execute("""
                SELECT cumulative_return_pct
                FROM jeoningu_trades
                ORDER BY id DESC
                LIMIT 1
            """) as cursor:
                latest = await cursor.fetchone()
                cumulative_return = latest['cumulative_return_pct'] if latest else 0.0

            # Calculate average return per trade
            avg_return = sum(t['profit_loss_pct'] for t in sell_trades) / total_trades if total_trades > 0 else 0.0

            metrics = {
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "win_rate": (winning_trades / total_trades * 100) if total_trades > 0 else 0.0,
                "cumulative_return": cumulative_return,
                "avg_return_per_trade": avg_return
            }

            return metrics

    async def get_dashboard_summary(self) -> Dict[str, Any]:
        """
        Get summary data for dashboard visualization

        Returns:
            Dictionary with summary data
        """
        metrics = await self.calculate_performance_metrics()
        recent_trades = await self.get_trade_history(limit=20)
        current_position = await self.get_current_position()
        latest_balance = await self.get_latest_balance()

        return {
            "performance": metrics,
            "recent_trades": recent_trades,
            "current_position": current_position,
            "latest_balance": latest_balance,
            "generated_at": datetime.now().isoformat()
        }


# Utility functions

async def init_database():
    """Initialize database (call once on first run)"""
    db = JeoninguTradingDB()
    await db.initialize()
    logger.info("Database initialized successfully")


# Test function
async def test_database():
    """Test database operations"""
    db = JeoninguTradingDB()
    await db.initialize()

    # Test initial trade
    trade_data = {
        "video_id": "test123",
        "video_title": "Test Video",
        "video_date": "2025-11-23",
        "video_url": "https://youtube.com/watch?v=test123",
        "analyzed_date": datetime.now().isoformat(),
        "jeon_sentiment": "상승",
        "jeon_reasoning": "긍정적 지표",
        "contrarian_action": "인버스매수",
        "trade_type": "BUY",
        "stock_code": "114800",
        "stock_name": "KODEX 인버스",
        "quantity": 100,
        "price": 10000,
        "amount": 1000000,
        "cumulative_balance": 10000000,
        "notes": "Test trade"
    }
    trade_id = await db.insert_trade(trade_data)

    # Check current position
    position = await db.get_current_position()
    print(f"Current position: {position}")

    # Get metrics
    metrics = await db.calculate_performance_metrics()
    print(f"Metrics: {metrics}")

    print(f"✅ Test completed. Trade ID: {trade_id}")


if __name__ == "__main__":
    asyncio.run(test_database())
