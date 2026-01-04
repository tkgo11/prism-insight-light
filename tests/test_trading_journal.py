#!/usr/bin/env python3
"""
Test Trading Journal System

This module tests the trading journal functionality including:
1. Database table creation
2. Journal entry creation
3. Context retrieval for buy decisions
4. Score adjustment calculation
"""

import asyncio
import json
import logging
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from stock_tracking_agent import StockTrackingAgent

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TestTradingJournalTables:
    """Test trading journal database tables"""

    def setup_method(self):
        """Set up test fixtures"""
        # Create a temporary database file
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_table_creation(self):
        """Test that trading_journal table is created correctly"""
        agent = StockTrackingAgent(db_path=self.db_path)

        # Mock the trading_agent to avoid MCP initialization
        agent.trading_agent = MagicMock()

        # Initialize the agent (creates tables)
        await agent.initialize(language="ko")

        # Check that trading_journal table exists
        agent.cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='trading_journal'
        """)
        result = agent.cursor.fetchone()
        assert result is not None, "trading_journal table should exist"

        # Check table columns
        agent.cursor.execute("PRAGMA table_info(trading_journal)")
        columns = {row[1] for row in agent.cursor.fetchall()}

        expected_columns = {
            'id', 'ticker', 'company_name', 'trade_date', 'trade_type',
            'buy_price', 'buy_date', 'buy_scenario', 'buy_market_context',
            'sell_price', 'sell_reason', 'profit_rate', 'holding_days',
            'situation_analysis', 'judgment_evaluation', 'lessons', 'pattern_tags',
            'one_line_summary', 'confidence_score', 'compression_layer',
            'compressed_summary', 'created_at', 'last_compressed_at'
        }

        assert expected_columns.issubset(columns), f"Missing columns: {expected_columns - columns}"

        # Clean up
        agent.conn.close()

    @pytest.mark.asyncio
    async def test_intuitions_table_creation(self):
        """Test that trading_intuitions table is created correctly"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Check that trading_intuitions table exists
        agent.cursor.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name='trading_intuitions'
        """)
        result = agent.cursor.fetchone()
        assert result is not None, "trading_intuitions table should exist"

        agent.conn.close()


class TestJournalContext:
    """Test journal context retrieval"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_empty_context(self):
        """Test context retrieval when no journal entries exist"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Get context for a stock with no history
        context = agent._get_relevant_journal_context(
            ticker="005930",
            sector="반도체"
        )

        # Should return empty string when no entries
        assert context == "", "Context should be empty when no journal entries exist"

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_context_with_entries(self):
        """Test context retrieval when journal entries exist"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert test journal entry
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        agent.cursor.execute("""
            INSERT INTO trading_journal
            (ticker, company_name, trade_date, trade_type,
             buy_price, buy_date, buy_scenario, sell_price,
             sell_reason, profit_rate, holding_days,
             situation_analysis, judgment_evaluation, lessons, pattern_tags,
             one_line_summary, confidence_score, compression_layer, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "005930", "삼성전자", now, "sell",
            70000, now, '{"sector": "반도체", "buy_score": 8}', 75000,
            "목표가 달성", 7.14, 10,
            '{"buy_context_summary": "Test buy context"}',
            '{"buy_quality": "적절"}',
            '[{"condition": "급등 후", "action": "분할 매도", "reason": "변동성"}]',
            '["급등후조정", "분할매도"]',
            "급등 후 분할 매도로 수익 실현",
            0.8, 1, now
        ))
        agent.conn.commit()

        # Get context
        context = agent._get_relevant_journal_context(
            ticker="005930",
            sector="반도체"
        )

        # Verify context contains expected information
        # Context format: includes profit rate, holding days, and summary
        assert "수익률" in context or "7.1" in context, "Context should contain profit info"
        assert "급등" in context or "분할 매도" in context, "Context should contain summary"
        assert "동일 종목" in context, "Context should have same stock section"

        agent.conn.close()


class TestScoreAdjustment:
    """Test score adjustment calculation"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_no_adjustment_without_history(self):
        """Test no adjustment when no trading history exists"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        adjustment, reasons = agent._get_score_adjustment_from_context(
            ticker="005930",
            sector="반도체"
        )

        assert adjustment == 0, "No adjustment without history"
        assert len(reasons) == 0, "No reasons without history"

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_negative_adjustment_for_losses(self):
        """Test negative adjustment for stocks with loss history"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert test entries with losses
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(3):
            agent.cursor.execute("""
                INSERT INTO trading_journal
                (ticker, company_name, trade_date, trade_type,
                 profit_rate, one_line_summary, compression_layer, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "005930", "삼성전자", now, "sell",
                -8.0 - i,  # Loss of -8%, -9%, -10%
                "손절 매도", 1, now
            ))
        agent.conn.commit()

        adjustment, reasons = agent._get_score_adjustment_from_context(
            ticker="005930",
            sector="반도체"
        )

        assert adjustment < 0, "Should have negative adjustment for losses"
        assert len(reasons) > 0, "Should have reasons for adjustment"

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_positive_adjustment_for_gains(self):
        """Test positive adjustment for stocks with profit history"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert test entries with profits
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(3):
            agent.cursor.execute("""
                INSERT INTO trading_journal
                (ticker, company_name, trade_date, trade_type,
                 profit_rate, one_line_summary, compression_layer, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                "005930", "삼성전자", now, "sell",
                12.0 + i,  # Profit of 12%, 13%, 14%
                "목표가 달성", 1, now
            ))
        agent.conn.commit()

        adjustment, reasons = agent._get_score_adjustment_from_context(
            ticker="005930",
            sector="반도체"
        )

        assert adjustment > 0, "Should have positive adjustment for gains"
        assert len(reasons) > 0, "Should have reasons for adjustment"

        agent.conn.close()


class TestParseJournalResponse:
    """Test journal response parsing"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_parse_valid_json(self):
        """Test parsing valid JSON response"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        response = '''
```json
{
    "situation_analysis": {
        "buy_context_summary": "Test buy context",
        "sell_context_summary": "Test sell context"
    },
    "judgment_evaluation": {
        "buy_quality": "적절"
    },
    "lessons": [
        {"condition": "급등 후", "action": "분할 매도", "reason": "변동성"}
    ],
    "pattern_tags": ["급등후조정"],
    "one_line_summary": "급등 후 분할 매도 성공",
    "confidence_score": 0.8
}
```
'''
        result = agent._parse_journal_response(response)

        assert "situation_analysis" in result
        assert result["one_line_summary"] == "급등 후 분할 매도 성공"
        assert result["confidence_score"] == 0.8

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_parse_invalid_json(self):
        """Test parsing invalid JSON response returns default structure"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        response = "This is not valid JSON at all"

        result = agent._parse_journal_response(response)

        # Should return default structure
        assert "situation_analysis" in result
        assert "lessons" in result
        assert isinstance(result["lessons"], list)

        agent.conn.close()


class TestCompression:
    """Test memory compression functionality"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_db = tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()

    def teardown_method(self):
        """Clean up test fixtures"""
        try:
            os.unlink(self.db_path)
        except:
            pass

    @pytest.mark.asyncio
    async def test_compression_stats_empty(self):
        """Test compression stats with no entries"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        stats = agent.get_compression_stats()

        assert 'entries_by_layer' in stats
        assert stats['entries_by_layer']['layer1_detailed'] == 0
        assert stats['active_intuitions'] == 0

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_compression_stats_with_entries(self):
        """Test compression stats with entries"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        # Insert test entries
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(5):
            agent.cursor.execute("""
                INSERT INTO trading_journal
                (ticker, company_name, trade_date, trade_type,
                 profit_rate, one_line_summary, compression_layer, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                f"00593{i}", f"테스트종목{i}", now, "sell",
                5.0 + i, f"테스트 요약 {i}", 1, now
            ))
        agent.conn.commit()

        stats = agent.get_compression_stats()

        assert stats['entries_by_layer']['layer1_detailed'] == 5
        assert stats['oldest_uncompressed'] is not None

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_save_intuition(self):
        """Test saving intuition to database"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        intuition = {
            "category": "pattern",
            "subcategory": "반도체",
            "condition": "거래량 급감 3일 연속",
            "insight": "추세 전환 가능성 높음",
            "confidence": 0.8,
            "supporting_trades": 5,
            "success_rate": 0.75
        }

        result = agent._save_intuition(intuition, [1, 2, 3])
        assert result is True

        # Verify saved
        agent.cursor.execute("SELECT * FROM trading_intuitions WHERE category = 'pattern'")
        saved = agent.cursor.fetchone()

        assert saved is not None
        assert saved['condition'] == "거래량 급감 3일 연속"
        assert saved['confidence'] == 0.8

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_save_duplicate_intuition_updates(self):
        """Test that saving duplicate intuition updates existing one"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        intuition = {
            "category": "pattern",
            "condition": "테스트 조건",
            "insight": "테스트 직관",
            "confidence": 0.6,
            "supporting_trades": 3,
            "success_rate": 0.7
        }

        # Save first time
        agent._save_intuition(intuition, [1])

        # Save second time with higher confidence
        intuition["confidence"] = 0.9
        intuition["supporting_trades"] = 5
        agent._save_intuition(intuition, [2, 3])

        # Should only have one entry, updated
        agent.cursor.execute("SELECT COUNT(*) FROM trading_intuitions")
        count = agent.cursor.fetchone()[0]
        assert count == 1

        agent.cursor.execute("SELECT supporting_trades FROM trading_intuitions")
        updated = agent.cursor.fetchone()
        assert updated['supporting_trades'] > 3  # Should be increased

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_generate_simple_summary(self):
        """Test simple summary generation"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        entry = {
            "one_line_summary": "급등 후 분할 매도로 수익 실현",
            "buy_scenario": '{"sector": "반도체"}',
            "profit_rate": 7.5
        }

        summary = agent._generate_simple_summary(entry)
        assert "급등" in summary or "분할" in summary

        # Test without summary
        entry2 = {
            "buy_scenario": '{"sector": "반도체"}',
            "profit_rate": -3.5
        }

        summary2 = agent._generate_simple_summary(entry2)
        assert "반도체" in summary2 or "손실" in summary2

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_format_entries_for_compression(self):
        """Test entry formatting for compression"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        entries = [
            {
                "id": 1,
                "ticker": "005930",
                "company_name": "삼성전자",
                "profit_rate": 5.5,
                "one_line_summary": "테스트 요약",
                "lessons": '[{"action": "교훈1"}]',
                "pattern_tags": '["태그1", "태그2"]'
            }
        ]

        formatted = agent._format_entries_for_compression(entries)

        assert "005930" in formatted
        assert "삼성전자" in formatted
        assert "5.5%" in formatted or "5.5" in formatted
        assert "교훈1" in formatted

        agent.conn.close()

    @pytest.mark.asyncio
    async def test_parse_compression_response(self):
        """Test parsing compression response"""
        agent = StockTrackingAgent(db_path=self.db_path)
        agent.trading_agent = MagicMock()
        await agent.initialize(language="ko")

        response = '''
```json
{
    "compressed_entries": [
        {
            "original_ids": [1, 2],
            "compressed_summary": "테스트 압축 요약",
            "key_lessons": ["교훈1", "교훈2"]
        }
    ],
    "new_intuitions": [
        {
            "category": "pattern",
            "condition": "테스트 조건",
            "insight": "테스트 직관",
            "confidence": 0.8
        }
    ]
}
```
'''
        result = agent._parse_compression_response(response)

        assert len(result['compressed_entries']) == 1
        assert result['compressed_entries'][0]['compressed_summary'] == "테스트 압축 요약"
        assert len(result['new_intuitions']) == 1
        assert result['new_intuitions'][0]['confidence'] == 0.8

        agent.conn.close()


def run_quick_test():
    """Run a quick integration test"""
    logger.info("Starting quick trading journal test")

    # Create temporary database
    with tempfile.NamedTemporaryFile(suffix='.sqlite', delete=False) as f:
        db_path = f.name

    async def async_test():
        try:
            agent = StockTrackingAgent(db_path=db_path)
            agent.trading_agent = MagicMock()

            # Initialize
            await agent.initialize(language="ko")
            logger.info("Agent initialized successfully")

            # Check tables exist
            agent.cursor.execute("""
                SELECT name FROM sqlite_master WHERE type='table'
            """)
            tables = [row[0] for row in agent.cursor.fetchall()]
            logger.info(f"Created tables: {tables}")

            assert "trading_journal" in tables, "trading_journal table missing"
            assert "trading_intuitions" in tables, "trading_intuitions table missing"

            # Test context retrieval (empty)
            context = agent._get_relevant_journal_context("005930", "반도체")
            logger.info(f"Empty context test: {'PASS' if context == '' else 'FAIL'}")

            # Test score adjustment (no history)
            adjustment, reasons = agent._get_score_adjustment_from_context("005930", "반도체")
            logger.info(f"No adjustment test: {'PASS' if adjustment == 0 else 'FAIL'}")

            # Test JSON parsing
            test_json = '{"one_line_summary": "테스트", "confidence_score": 0.9}'
            parsed = agent._parse_journal_response(test_json)
            logger.info(f"JSON parse test: {'PASS' if parsed.get('confidence_score') == 0.9 else 'FAIL'}")

            # Test compression stats
            stats = agent.get_compression_stats()
            logger.info(f"Compression stats test: {'PASS' if 'entries_by_layer' in stats else 'FAIL'}")

            # Test save intuition
            intuition = {
                "category": "test",
                "condition": "테스트 조건",
                "insight": "테스트 직관",
                "confidence": 0.7
            }
            save_result = agent._save_intuition(intuition, [1])
            logger.info(f"Save intuition test: {'PASS' if save_result else 'FAIL'}")

            # Test simple summary generation
            entry = {"profit_rate": 5.0, "buy_scenario": '{"sector": "테스트"}'}
            summary = agent._generate_simple_summary(entry)
            logger.info(f"Simple summary test: {'PASS' if summary else 'FAIL'}")

            agent.conn.close()
            logger.info("Quick test completed successfully!")
            return True

        except Exception as e:
            logger.error(f"Quick test failed: {e}")
            import traceback
            traceback.print_exc()
            return False

        finally:
            # Cleanup
            try:
                os.unlink(db_path)
            except:
                pass

    return asyncio.run(async_test())


if __name__ == "__main__":
    # Run quick test when executed directly
    success = run_quick_test()
    sys.exit(0 if success else 1)
