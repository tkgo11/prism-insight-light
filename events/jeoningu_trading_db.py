"""
Jeon Ingu Contrarian Trading - SQLite Database Schema and Operations

This module handles database operations for tracking Jeon Ingu's contrarian trading simulation.
Stores video analysis, trading decisions, and performance metrics.
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
        """Initialize database tables"""
        async with aiosqlite.connect(self.db_path) as db:
            # Videos table - stores analyzed YouTube videos
            await db.execute("""
                CREATE TABLE IF NOT EXISTS videos (
                    video_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    published_date TEXT NOT NULL,
                    analyzed_date TEXT NOT NULL,
                    video_url TEXT NOT NULL,
                    transcript_summary TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Analysis results table - stores AI analysis of videos
            await db.execute("""
                CREATE TABLE IF NOT EXISTS analysis_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    jeon_prediction TEXT NOT NULL,
                    jeon_reasoning TEXT,
                    contrarian_strategy TEXT NOT NULL,
                    contrarian_reasoning TEXT,
                    target_stocks TEXT,
                    sentiment_score REAL,
                    confidence_score REAL,
                    raw_analysis_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (video_id) REFERENCES videos(video_id)
                )
            """)

            # Trades table - stores all buy/sell transactions
            await db.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    analysis_id INTEGER NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT NOT NULL,
                    trade_type TEXT NOT NULL,
                    trade_date TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    total_amount REAL NOT NULL,
                    related_buy_id INTEGER,
                    profit_loss REAL,
                    profit_loss_rate REAL,
                    cumulative_return REAL,
                    strategy_note TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (video_id) REFERENCES videos(video_id),
                    FOREIGN KEY (analysis_id) REFERENCES analysis_results(id),
                    FOREIGN KEY (related_buy_id) REFERENCES trades(id)
                )
            """)

            # Portfolio table - current holdings
            await db.execute("""
                CREATE TABLE IF NOT EXISTS portfolio (
                    stock_code TEXT PRIMARY KEY,
                    stock_name TEXT NOT NULL,
                    buy_trade_id INTEGER NOT NULL,
                    video_id TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    avg_buy_price REAL NOT NULL,
                    total_investment REAL NOT NULL,
                    buy_date TEXT NOT NULL,
                    strategy_note TEXT,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (buy_trade_id) REFERENCES trades(id),
                    FOREIGN KEY (video_id) REFERENCES videos(video_id)
                )
            """)

            # Performance metrics table - overall performance tracking
            await db.execute("""
                CREATE TABLE IF NOT EXISTS performance_metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    calculation_date TEXT NOT NULL,
                    total_trades INTEGER NOT NULL,
                    winning_trades INTEGER NOT NULL,
                    losing_trades INTEGER NOT NULL,
                    win_rate REAL NOT NULL,
                    total_invested REAL NOT NULL,
                    total_returns REAL NOT NULL,
                    cumulative_return REAL NOT NULL,
                    avg_return_per_trade REAL NOT NULL,
                    max_drawdown REAL,
                    sharpe_ratio REAL,
                    metrics_json TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Telegram messages table - track sent messages
            await db.execute("""
                CREATE TABLE IF NOT EXISTS telegram_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    analysis_id INTEGER NOT NULL,
                    message_text TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    sent_at TEXT NOT NULL,
                    message_id INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (video_id) REFERENCES videos(video_id),
                    FOREIGN KEY (analysis_id) REFERENCES analysis_results(id)
                )
            """)

            await db.commit()
            logger.info(f"Database initialized at {self.db_path}")

    async def insert_video(self, video_data: Dict[str, Any]) -> str:
        """Insert new video record"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO videos (video_id, title, published_date, analyzed_date,
                                   video_url, transcript_summary)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                video_data['video_id'],
                video_data['title'],
                video_data['published_date'],
                video_data['analyzed_date'],
                video_data['video_url'],
                video_data.get('transcript_summary', '')
            ))
            await db.commit()
            logger.info(f"Video inserted: {video_data['video_id']}")
            return video_data['video_id']

    async def insert_analysis(self, analysis_data: Dict[str, Any]) -> int:
        """Insert analysis result"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO analysis_results
                (video_id, jeon_prediction, jeon_reasoning, contrarian_strategy,
                 contrarian_reasoning, target_stocks, sentiment_score,
                 confidence_score, raw_analysis_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                analysis_data['video_id'],
                analysis_data['jeon_prediction'],
                analysis_data.get('jeon_reasoning', ''),
                analysis_data['contrarian_strategy'],
                analysis_data.get('contrarian_reasoning', ''),
                json.dumps(analysis_data.get('target_stocks', []), ensure_ascii=False),
                analysis_data.get('sentiment_score'),
                analysis_data.get('confidence_score'),
                json.dumps(analysis_data.get('raw_analysis', {}), ensure_ascii=False)
            ))
            await db.commit()
            analysis_id = cursor.lastrowid
            logger.info(f"Analysis inserted: ID {analysis_id}")
            return analysis_id

    async def insert_trade(self, trade_data: Dict[str, Any]) -> int:
        """Insert trade record"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO trades
                (video_id, analysis_id, stock_code, stock_name, trade_type,
                 trade_date, quantity, price, total_amount, related_buy_id,
                 profit_loss, profit_loss_rate, cumulative_return, strategy_note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data['video_id'],
                trade_data['analysis_id'],
                trade_data['stock_code'],
                trade_data['stock_name'],
                trade_data['trade_type'],
                trade_data['trade_date'],
                trade_data['quantity'],
                trade_data['price'],
                trade_data['total_amount'],
                trade_data.get('related_buy_id'),
                trade_data.get('profit_loss'),
                trade_data.get('profit_loss_rate'),
                trade_data.get('cumulative_return'),
                trade_data.get('strategy_note', '')
            ))
            await db.commit()
            trade_id = cursor.lastrowid
            logger.info(f"Trade inserted: ID {trade_id}, Type {trade_data['trade_type']}")
            return trade_id

    async def update_portfolio(self, stock_code: str, portfolio_data: Dict[str, Any]):
        """Update or insert portfolio position"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO portfolio
                (stock_code, stock_name, buy_trade_id, video_id, quantity,
                 avg_buy_price, total_investment, buy_date, strategy_note, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                stock_code,
                portfolio_data['stock_name'],
                portfolio_data['buy_trade_id'],
                portfolio_data['video_id'],
                portfolio_data['quantity'],
                portfolio_data['avg_buy_price'],
                portfolio_data['total_investment'],
                portfolio_data['buy_date'],
                portfolio_data.get('strategy_note', ''),
                datetime.now().isoformat()
            ))
            await db.commit()
            logger.info(f"Portfolio updated: {stock_code}")

    async def remove_from_portfolio(self, stock_code: str):
        """Remove stock from portfolio (after sell)"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM portfolio WHERE stock_code = ?", (stock_code,))
            await db.commit()
            logger.info(f"Removed from portfolio: {stock_code}")

    async def get_portfolio(self) -> List[Dict[str, Any]]:
        """Get current portfolio"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM portfolio ORDER BY buy_date DESC") as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_trade_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get trade history"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM trades
                ORDER BY trade_date DESC, created_at DESC
                LIMIT ?
            """, (limit,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_video_analysis(self, video_id: str) -> Optional[Dict[str, Any]]:
        """Get analysis for a specific video"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT a.*, v.title, v.video_url
                FROM analysis_results a
                JOIN videos v ON a.video_id = v.video_id
                WHERE a.video_id = ?
                ORDER BY a.created_at DESC
                LIMIT 1
            """, (video_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def calculate_performance_metrics(self) -> Dict[str, Any]:
        """Calculate overall performance metrics"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Get all completed trades (buy-sell pairs)
            async with db.execute("""
                SELECT * FROM trades
                WHERE trade_type = 'SELL'
                ORDER BY trade_date
            """) as cursor:
                sell_trades = await cursor.fetchall()

            if not sell_trades:
                return {
                    "total_trades": 0,
                    "winning_trades": 0,
                    "losing_trades": 0,
                    "win_rate": 0.0,
                    "cumulative_return": 0.0
                }

            total_trades = len(sell_trades)
            winning_trades = sum(1 for t in sell_trades if t['profit_loss'] > 0)
            losing_trades = sum(1 for t in sell_trades if t['profit_loss'] <= 0)

            # Calculate cumulative return (latest)
            latest_return = sell_trades[-1]['cumulative_return'] if sell_trades else 0.0

            # Calculate average return per trade
            avg_return = sum(t['profit_loss_rate'] for t in sell_trades) / total_trades if total_trades > 0 else 0.0

            metrics = {
                "calculation_date": datetime.now().isoformat(),
                "total_trades": total_trades,
                "winning_trades": winning_trades,
                "losing_trades": losing_trades,
                "win_rate": (winning_trades / total_trades * 100) if total_trades > 0 else 0.0,
                "cumulative_return": latest_return,
                "avg_return_per_trade": avg_return
            }

            # Save to performance_metrics table
            await db.execute("""
                INSERT INTO performance_metrics
                (calculation_date, total_trades, winning_trades, losing_trades,
                 win_rate, total_invested, total_returns, cumulative_return,
                 avg_return_per_trade, metrics_json)
                VALUES (?, ?, ?, ?, ?, 0, 0, ?, ?, ?)
            """, (
                metrics['calculation_date'],
                metrics['total_trades'],
                metrics['winning_trades'],
                metrics['losing_trades'],
                metrics['win_rate'],
                metrics['cumulative_return'],
                metrics['avg_return_per_trade'],
                json.dumps(metrics, ensure_ascii=False)
            ))
            await db.commit()

            return metrics

    async def insert_telegram_message(self, message_data: Dict[str, Any]) -> int:
        """Record sent Telegram message"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("""
                INSERT INTO telegram_messages
                (video_id, analysis_id, message_text, channel_id, sent_at, message_id)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                message_data['video_id'],
                message_data['analysis_id'],
                message_data['message_text'],
                message_data['channel_id'],
                message_data['sent_at'],
                message_data.get('message_id')
            ))
            await db.commit()
            return cursor.lastrowid


# Utility functions for common operations

async def init_database():
    """Initialize database (call once on first run)"""
    db = JeoninguTradingDB()
    await db.initialize()
    logger.info("Database initialized successfully")


async def get_current_portfolio_value(db: JeoninguTradingDB) -> Dict[str, Any]:
    """Get current portfolio with live market values"""
    portfolio = await db.get_portfolio()

    # TODO: Fetch current prices from pykrx and calculate current values
    total_investment = sum(p['total_investment'] for p in portfolio)

    return {
        "positions": portfolio,
        "total_positions": len(portfolio),
        "total_investment": total_investment,
        "current_value": 0.0,  # To be calculated with live prices
        "unrealized_pnl": 0.0
    }


async def get_dashboard_summary(db: JeoninguTradingDB) -> Dict[str, Any]:
    """Get summary data for dashboard visualization"""
    metrics = await db.calculate_performance_metrics()
    portfolio = await db.get_portfolio()
    recent_trades = await db.get_trade_history(limit=20)

    return {
        "performance": metrics,
        "portfolio": portfolio,
        "recent_trades": recent_trades,
        "generated_at": datetime.now().isoformat()
    }


# Test function
async def test_database():
    """Test database operations"""
    db = JeoninguTradingDB()
    await db.initialize()

    # Test video insert
    video_data = {
        "video_id": "test123",
        "title": "Test Video",
        "published_date": "2025-11-23",
        "analyzed_date": datetime.now().isoformat(),
        "video_url": "https://youtube.com/watch?v=test123",
        "transcript_summary": "Test summary"
    }
    await db.insert_video(video_data)

    # Test analysis insert
    analysis_data = {
        "video_id": "test123",
        "jeon_prediction": "시장 상승 예측",
        "jeon_reasoning": "긍정적 지표",
        "contrarian_strategy": "인버스 ETF 매수",
        "contrarian_reasoning": "역발상 전략",
        "target_stocks": [{"code": "252670", "name": "KODEX 200선물인버스2X"}],
        "confidence_score": 0.75
    }
    analysis_id = await db.insert_analysis(analysis_data)

    print(f"✅ Test completed. Analysis ID: {analysis_id}")


if __name__ == "__main__":
    asyncio.run(test_database())
