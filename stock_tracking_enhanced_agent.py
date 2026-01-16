from dotenv import load_dotenv
load_dotenv()  # .env 파일에서 환경변수 로드

import numpy as np
from scipy import stats
from typing import List, Tuple, Dict, Any
from datetime import datetime, timedelta
from stock_tracking_agent import StockTrackingAgent
import logging
import json
import traceback
import re

from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

# Import core agents
from cores.agents.trading_agents import create_sell_decision_agent

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"enhanced_stock_tracking_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)


class EnhancedStockTrackingAgent(StockTrackingAgent):
    """Enhanced stock tracking and trading agent"""

    def __init__(self, db_path: str = "stock_tracking_db.sqlite", telegram_token: str = None):
        """Initialize agent"""
        super().__init__(db_path, telegram_token)
        # Market condition storage variable (1: bull market, 0: neutral, -1: bear market)
        self.simple_market_condition = 0
        # Volatility table (store volatility per stock)
        self.volatility_table = {}

    async def initialize(self, language: str = "ko"):
        """
        Create necessary tables and initialize

        Args:
            language: Language code for agents (default: "ko")
        """
        await super().initialize(language)

        # Initialize sell decision agent with language
        self.sell_decision_agent = create_sell_decision_agent(language=language)

        # Create market condition analysis table
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_condition (
                date TEXT PRIMARY KEY,
                kospi_index REAL,
                kosdaq_index REAL,
                condition INTEGER,  -- 1: bull market, 0: neutral, -1: bear market
                volatility REAL
            )
        """)

        # TODO: Modify to keep only 1 month of data and delete the rest
        # Create watchlist (hold/watch) tracking table
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                company_name TEXT NOT NULL,
                current_price REAL NOT NULL,
                analyzed_date TEXT NOT NULL,
                buy_score INTEGER NOT NULL,
                min_score INTEGER NOT NULL,
                decision TEXT NOT NULL,
                skip_reason TEXT NOT NULL,
                target_price REAL,
                stop_loss REAL,
                investment_period TEXT,
                sector TEXT,
                scenario TEXT,
                portfolio_analysis TEXT,
                valuation_analysis TEXT,
                sector_outlook TEXT,
                market_condition TEXT,
                rationale TEXT,
                trigger_type TEXT,
                trigger_mode TEXT,
                risk_reward_ratio REAL,
                was_traded INTEGER DEFAULT 0
            )
        """)

        # Auto-migrate: Add missing columns to existing watchlist_history table
        await self._migrate_watchlist_history_columns()

        # Create holding decision table (store AI holding/selling decisions)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS holding_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                decision_date TEXT NOT NULL,
                decision_time TEXT NOT NULL,

                current_price REAL NOT NULL,
                should_sell BOOLEAN NOT NULL,
                sell_reason TEXT,
                confidence INTEGER,

                technical_trend TEXT,
                volume_analysis TEXT,
                market_condition_impact TEXT,
                time_factor TEXT,

                portfolio_adjustment_needed BOOLEAN,
                adjustment_reason TEXT,
                new_target_price REAL,
                new_stop_loss REAL,
                adjustment_urgency TEXT,

                full_json_data TEXT NOT NULL,

                created_at TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (ticker) REFERENCES stock_holdings(ticker)
            )
        """)

        self.conn.commit()

        # Run market condition analysis
        await self._analyze_simple_market_condition()

        # Clean up old watchlist data (older than 1 month)
        await self._cleanup_old_watchlist()

        return True

    async def _analyze_simple_market_condition(self):
        """Analyze market condition (bull/bear market)"""
        try:
            from krx_data_client import get_index_ohlcv_by_date
            import datetime as dt

            # Today's date
            today = dt.datetime.now().strftime("%Y%m%d")

            # One month ago
            one_month_ago = (dt.datetime.now() - dt.timedelta(days=30)).strftime("%Y%m%d")

            # Get KOSPI and KOSDAQ index data
            kospi_df = get_index_ohlcv_by_date(one_month_ago, today, "1001")
            kosdaq_df = get_index_ohlcv_by_date(one_month_ago, today, "2001")

            # Analyze index trends
            kospi_trend = self._calculate_trend(kospi_df['Close'])
            kosdaq_trend = self._calculate_trend(kosdaq_df['Close'])

            # Determine overall market condition
            # Bull market (1) if both trending up, bear market (-1) if both down, neutral (0) otherwise
            if kospi_trend > 0 and kosdaq_trend > 0:
                market_condition = 1  # Bull market
            elif kospi_trend < 0 and kosdaq_trend < 0:
                market_condition = -1  # Bear market
            else:
                market_condition = 0  # Neutral

            # Calculate market volatility (average of KOSPI and KOSDAQ volatility)
            kospi_volatility = self._calculate_volatility(kospi_df['Close'])
            kosdaq_volatility = self._calculate_volatility(kosdaq_df['Close'])
            avg_volatility = (kospi_volatility + kosdaq_volatility) / 2

            # Store market condition
            self.simple_market_condition = market_condition

            # Save to DB
            current_date = dt.datetime.now().strftime("%Y-%m-%d")
            self.cursor.execute(
                """
                INSERT OR REPLACE INTO market_condition
                (date, kospi_index, kosdaq_index, condition, volatility)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    current_date,
                    kospi_df['Close'].iloc[-1],
                    kosdaq_df['Close'].iloc[-1],
                    market_condition,
                    avg_volatility
                )
            )
            self.conn.commit()

            logger.info(f"Market condition analysis complete: {'Bull' if market_condition == 1 else 'Bear' if market_condition == -1 else 'Neutral'}, Volatility: {avg_volatility:.2f}%")

            return market_condition, avg_volatility

        except Exception as e:
            logger.error(f"Error analyzing market condition: {str(e)}")
            return 0, 0  # Assume neutral on error

    async def _migrate_watchlist_history_columns(self):
        """Auto-migrate: Add missing columns to existing watchlist_history table"""
        try:
            # Get existing columns
            self.cursor.execute("PRAGMA table_info(watchlist_history)")
            existing_columns = {row[1] for row in self.cursor.fetchall()}

            # Define columns to add if missing (column_name, column_definition)
            columns_to_add = [
                ("trigger_type", "TEXT"),
                ("trigger_mode", "TEXT"),
                ("risk_reward_ratio", "REAL"),
                ("was_traded", "INTEGER DEFAULT 0"),
            ]

            for column_name, column_def in columns_to_add:
                if column_name not in existing_columns:
                    self.cursor.execute(
                        f"ALTER TABLE watchlist_history ADD COLUMN {column_name} {column_def}"
                    )
                    logger.info(f"Added column '{column_name}' to watchlist_history table")

            self.conn.commit()

        except Exception as e:
            logger.error(f"Error migrating watchlist_history columns: {str(e)}")

    async def _cleanup_old_watchlist(self):
        """Delete watchlist data older than 1 month"""
        try:
            one_month_ago = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            deleted = self.cursor.execute(
                "DELETE FROM watchlist_history WHERE date(analyzed_date) < ?",
                (one_month_ago,)
            ).rowcount
            self.conn.commit()

            if deleted > 0:
                logger.info(f"Deleted {deleted} old watchlist entries")

        except Exception as e:
            logger.error(f"Error cleaning watchlist: {str(e)}")

    def _calculate_trend(self, price_series):
        """Analyze price series trend (positive: uptrend, negative: downtrend)"""
        # Calculate trend using simple linear regression
        x = np.arange(len(price_series))
        slope, _, _, _, _ = stats.linregress(x, price_series)
        return slope

    def _calculate_volatility(self, price_series):
        """Calculate price series volatility (daily return std dev, annualized)"""
        daily_returns = price_series.pct_change().dropna()
        daily_volatility = daily_returns.std()
        return daily_volatility * 100  # Convert to percentage

    async def _get_stock_volatility(self, ticker):
        """개별 종목의 변동성 계산"""
        try:
            # Use cached volatility if available
            if ticker in self.volatility_table:
                return self.volatility_table[ticker]

            # Fetch 60 days of price data
            today = datetime.now()
            start_date = (today - timedelta(days=60)).strftime("%Y%m%d")
            end_date = today.strftime("%Y%m%d")

            # Fetch stock price data using krx_data_client
            from krx_data_client import get_market_ohlcv_by_date
            df = get_market_ohlcv_by_date(start_date, end_date, ticker)

            if df.empty:
                logger.warning(f"{ticker} Cannot fetch price data")
                return 15.0  # Default volatility (15%)

            # Calculate standard deviation of daily returns
            daily_returns = df['Close'].pct_change().dropna()
            volatility = daily_returns.std() * 100  # Convert to percentage

            # Store in volatility table
            self.volatility_table[ticker] = volatility

            return volatility

        except Exception as e:
            logger.error(f"{ticker} Error calculating volatility: {str(e)}")
            return 15.0  # Return default volatility on error

    async def _dynamic_stop_loss(self, ticker, buy_price):
        """종목별 변동성에 기반한 동적 손절 가격 계산"""
        try:
            # Get stock volatility
            volatility = await self._get_stock_volatility(ticker)

            # Calculate stop-loss width based on volatility (wider for higher volatility)
            # Apply volatility adjustment to base 5% stop-loss
            base_stop_loss_pct = 5.0

            # Relative volatility ratio vs market average (15% assumed)
            relative_volatility = volatility / 15.0

            # Calculate adjusted stop-loss (min 3%, max 15%)
            adjusted_stop_loss_pct = min(max(base_stop_loss_pct * relative_volatility, 3.0), 15.0)

            # Additional adjustment based on market condition
            if self.simple_market_condition == -1:  # Bear market
                adjusted_stop_loss_pct = adjusted_stop_loss_pct * 0.8  # Tighter
            elif self.simple_market_condition == 1:  # Bull market
                adjusted_stop_loss_pct = adjusted_stop_loss_pct * 1.2  # Wider

            # Calculate stop-loss price
            stop_loss = buy_price * (1 - adjusted_stop_loss_pct/100)

            logger.info(f"{ticker} Dynamic stop-loss calculated: {stop_loss:,.0f} KRW (volatility: {volatility:.2f}%, stop-loss width: {adjusted_stop_loss_pct:.2f}%)")

            return stop_loss

        except Exception as e:
            logger.error(f"{ticker} Error calculating dynamic stop-loss: {str(e)}")
            return buy_price * 0.95  # Apply default 5% stop-loss on error

    async def _dynamic_target_price(self, ticker, buy_price):
        """Calculate dynamic target price based on individual stock volatility"""
        try:
            # Get stock volatility
            volatility = await self._get_stock_volatility(ticker)

            # Calculate target price based on volatility (higher volatility → higher target)
            # Apply volatility adjustment to base 10% target return
            base_target_pct = 10.0

            # Relative volatility ratio vs market average (15% assumed)
            relative_volatility = volatility / 15.0

            # Calculate adjusted target return (min 5%, max 30%)
            adjusted_target_pct = min(max(base_target_pct * relative_volatility, 5.0), 30.0)

            # Additional adjustment based on market condition
            if self.simple_market_condition == 1:  # Bull market
                adjusted_target_pct = adjusted_target_pct * 1.3  # Higher
            elif self.simple_market_condition == -1:  # Bear market
                adjusted_target_pct = adjusted_target_pct * 0.7  # Lower

            # Calculate target price
            target_price = buy_price * (1 + adjusted_target_pct/100)

            logger.info(f"{ticker} Dynamic target price calculated: {target_price:,.0f} KRW (volatility: {volatility:.2f}%, target return: {adjusted_target_pct:.2f}%)")

            return target_price

        except Exception as e:
            logger.error(f"{ticker} Error calculating dynamic target: {str(e)}")
            return buy_price * 1.1  # Apply default 10% target return on error

    async def process_reports(self, pdf_report_paths: List[str]) -> Tuple[int, int]:
        """
        Process analysis reports and make buy/sell decisions

        Args:
            pdf_report_paths: List of pdf analysis report file paths

        Returns:
            Tuple[int, int]: Buy count, Sell count
        """
        try:
            logger.info(f"Starting processing of {len(pdf_report_paths)} reports")

            # Buy/Sell counters
            buy_count = 0
            sell_count = 0

            # 1. Update existing holdings and make sell decisions
            sold_stocks = await self.update_holdings()
            sell_count = len(sold_stocks)

            if sold_stocks:
                logger.info(f"{len(sold_stocks)} stocks sold")
                for stock in sold_stocks:
                    logger.info(f"Sold: {stock['company_name']}({stock['ticker']}) - Return: {stock['profit_rate']:.2f}% / Reason: {stock['reason']}")
            else:
                logger.info("No stocks sold")

            # 2. Analyze new reports and make buy decisions
            for pdf_report_path in pdf_report_paths:
                # Analyze report
                analysis_result = await self.analyze_report(pdf_report_path)

                if not analysis_result.get("success", False):
                    logger.error(f"Report analysis failed: {pdf_report_path} - {analysis_result.get('error', 'Unknown error')}")
                    continue

                # Skip if already holding this stock (no telegram message for already held stocks)
                if analysis_result.get("decision") == "보유 중":
                    logger.info(f"Skipping stock already in holdings: {analysis_result.get('ticker')} - {analysis_result.get('company_name')}")
                    continue

                # Stock information and scenario
                ticker = analysis_result.get("ticker")
                company_name = analysis_result.get("company_name")
                current_price = analysis_result.get("current_price", 0)
                scenario = analysis_result.get("scenario", {})
                sector = analysis_result.get("sector", "알 수 없음")
                sector_diverse = analysis_result.get("sector_diverse", True)
                rank_change_percentage = analysis_result.get("rank_change_percentage", 0)
                rank_change_msg = analysis_result.get("rank_change_msg", "")

                # Check entry decision
                buy_score = scenario.get("buy_score", 0)
                min_score = scenario.get("min_score", 0)
                decision = analysis_result.get("decision")
                logger.info(f"Buy score check: {company_name}({ticker}) - Score: {buy_score}, Min required score: {min_score}")

                # Generate message if not buying (watch/insufficient score/sector constraints)
                if decision != "진입" or buy_score < min_score or not sector_diverse:
                    # Determine reason for not buying
                    reason = ""
                    if not sector_diverse:
                        reason = f"산업군 '{sector}' 과다 투자 방지"
                    elif buy_score < min_score:
                        if decision == "진입":
                            decision = "미진입"  # "진입"에서 "미진입"으로 변경
                            logger.info(f"Decision changed due to insufficient buy score: {company_name}({ticker}) - Enter → Wait (Score: {buy_score} < {min_score})")
                        reason = f"매수 점수 부족 ({buy_score} < {min_score})"
                    elif decision != "진입":
                        reason = f"분석 결정이 '미진입'"

                    # 시장 상태 정보
                    market_condition_text = scenario.get("market_condition")

                    # 미진입 메시지 생성
                    skip_message = f"⚠️ 매수 보류: {company_name}({ticker})\n" \
                                   f"현재가: {current_price:,.0f}원\n" \
                                   f"매수 Score: {buy_score}/10\n" \
                                   f"결정: {decision}\n" \
                                   f"시장 상태: {market_condition_text}\n" \
                                   f"산업군: {scenario.get('sector', '알 수 없음')}\n" \
                                   f"보류 Reason: {reason}\n" \
                                   f"분석 의견: {scenario.get('rationale', '정보 없음')}"

                    self.message_queue.append(skip_message)
                    logger.info(f"Purchase deferred: {company_name}({ticker}) - {reason}")

                    # Save watch list stocks to watchlist_history table
                    await self._save_watchlist_item(
                        ticker=ticker,
                        company_name=company_name,
                        current_price=current_price,
                        buy_score=buy_score,
                        min_score=min_score,
                        decision=decision,
                        skip_reason=reason,
                        scenario=scenario,
                        sector=sector
                    )

                    continue

                # Process buy if entry decision
                if decision == "진입" and buy_score >= min_score and sector_diverse:
                    # Process buy
                    buy_success = await self.buy_stock(ticker, company_name, current_price, scenario, rank_change_msg)

                    if buy_success:
                        # Call actual account trading function (async)
                        from trading.domestic_stock_trading import AsyncTradingContext
                        async with AsyncTradingContext() as trading:
                            # Execute async buy
                            trade_result = await trading.async_buy_stock(stock_code=ticker)

                        if trade_result['success']:
                            logger.info(f"Actual purchase successful: {trade_result['message']}")
                        else:
                            logger.error(f"Actual purchase failed: {trade_result['message']}")

                        # [Optional] Publish buy signal via Redis Streams
                        # Auto-skipped if Redis not configured (requires UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN)
                        try:
                            from messaging.redis_signal_publisher import publish_buy_signal
                            await publish_buy_signal(
                                ticker=ticker,
                                company_name=company_name,
                                price=current_price,
                                scenario=scenario,
                                source="AI분석",
                                trade_result=trade_result
                            )
                        except Exception as signal_err:
                            logger.warning(f"Buy signal publish failed (non-critical): {signal_err}")

                        # [Optional] Publish buy signal via GCP Pub/Sub
                        # Auto-skipped if GCP not configured (requires GCP_PROJECT_ID, GCP_PUBSUB_TOPIC_ID)
                        try:
                            from messaging.gcp_pubsub_signal_publisher import publish_buy_signal as gcp_publish_buy_signal
                            await gcp_publish_buy_signal(
                                ticker=ticker,
                                company_name=company_name,
                                price=current_price,
                                scenario=scenario,
                                source="AI분석",
                                trade_result=trade_result
                            )
                        except Exception as signal_err:
                            logger.warning(f"GCP buy signal publish failed (non-critical): {signal_err}")

                    if buy_success:
                        buy_count += 1
                        logger.info(f"Purchase complete: {company_name}({ticker}) @ {current_price:,.0f} KRW")
                    else:
                        logger.warning(f"Purchase failed: {company_name}({ticker})")

            logger.info(f"Report processing complete - Purchased: {buy_count}items, Sold: {sell_count} items")
            return buy_count, sell_count

        except Exception as e:
            logger.error(f"Error processing reports: {str(e)}")
            logger.error(traceback.format_exc())
            return 0, 0

    async def buy_stock(self, ticker: str, company_name: str, current_price: float, scenario: Dict[str, Any], rank_change_msg: str = "") -> bool:
        """
        Stock buy processing (override parent class method)
        """
        try:
            # Calculate dynamically if target price/stop-loss is missing or 0 in scenario
            if scenario.get('target_price', 0) <= 0:
                target_price = await self._dynamic_target_price(ticker, current_price)
                scenario['target_price'] = target_price
                logger.info(f"{ticker} Dynamic target price calculated: {target_price:,.0f} KRW")

            if scenario.get('stop_loss', 0) <= 0:
                stop_loss = await self._dynamic_stop_loss(ticker, current_price)
                scenario['stop_loss'] = stop_loss
                logger.info(f"{ticker} Dynamic stop-loss calculated: {stop_loss:,.0f} KRW")

            # Call parent class's buy_stock method
            return await super().buy_stock(ticker, company_name, current_price, scenario, rank_change_msg)

        except Exception as e:
            logger.error(f"{ticker} Error during purchase processing: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _save_watchlist_item(
        self,
        ticker: str,
        company_name: str,
        current_price: float,
        buy_score: int,
        min_score: int,
        decision: str,
        skip_reason: str,
        scenario: Dict[str, Any],
        sector: str,
        was_traded: bool = False
    ) -> bool:
        """
        Save stocks not purchased to watchlist_history table and analysis_performance_tracker

        Args:
            ticker: Stock ticker
            company_name: Company name
            current_price: Current price
            buy_score: Buy score
            min_score: Minimum required score
            decision: Decision (entry/watch)
            skip_reason: Deferral reason
            scenario: Complete scenario information
            sector: Sector
            was_traded: Whether the stock was actually traded

        Returns:
            bool: Save success status
        """
        try:
            # Current time
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Extract necessary information from scenario
            target_price = scenario.get('target_price', 0)
            stop_loss = scenario.get('stop_loss', 0)
            investment_period = scenario.get('investment_period', '단기')
            portfolio_analysis = scenario.get('portfolio_analysis', '')
            valuation_analysis = scenario.get('valuation_analysis', '')
            sector_outlook = scenario.get('sector_outlook', '')
            market_condition = scenario.get('market_condition', '')
            rationale = scenario.get('rationale', '')

            # Get trigger info from parent's trigger_info_map
            trigger_info = getattr(self, 'trigger_info_map', {}).get(ticker, {})
            trigger_type = trigger_info.get('trigger_type', '')
            trigger_mode = trigger_info.get('trigger_mode', '')
            risk_reward_ratio = trigger_info.get('risk_reward_ratio', scenario.get('risk_reward_ratio', 0))

            # Save to watchlist_history with trigger info
            self.cursor.execute(
                """
                INSERT INTO watchlist_history
                (ticker, company_name, current_price, analyzed_date, buy_score, min_score,
                 decision, skip_reason, target_price, stop_loss, investment_period, sector,
                 scenario, portfolio_analysis, valuation_analysis, sector_outlook,
                 market_condition, rationale, trigger_type, trigger_mode, risk_reward_ratio, was_traded)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    company_name,
                    current_price,
                    now,
                    buy_score,
                    min_score,
                    decision,
                    skip_reason,
                    target_price,
                    stop_loss,
                    investment_period,
                    sector,
                    json.dumps(scenario, ensure_ascii=False),
                    portfolio_analysis,
                    valuation_analysis,
                    sector_outlook,
                    market_condition,
                    rationale,
                    trigger_type,
                    trigger_mode,
                    risk_reward_ratio,
                    1 if was_traded else 0
                )
            )

            # Get the last inserted ID for foreign key reference
            watchlist_id = self.cursor.lastrowid

            # Also save to analysis_performance_tracker for tracking
            self.cursor.execute(
                """
                INSERT INTO analysis_performance_tracker
                (watchlist_id, ticker, company_name, trigger_type, trigger_mode,
                 analyzed_date, analyzed_price, decision, was_traded, skip_reason,
                 buy_score, min_score, target_price, stop_loss, risk_reward_ratio,
                 tracking_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    watchlist_id,
                    ticker,
                    company_name,
                    trigger_type,
                    trigger_mode,
                    now,
                    current_price,
                    decision,
                    1 if was_traded else 0,
                    skip_reason,
                    buy_score,
                    min_score,
                    target_price,
                    stop_loss,
                    risk_reward_ratio,
                    now
                )
            )

            self.conn.commit()

            logger.info(f"{ticker}({company_name}) Watchlist save complete - Score: {buy_score}/{min_score}, Reason: {skip_reason}, Trigger: {trigger_type}")
            return True

        except Exception as e:
            logger.error(f"{ticker} Error saving watchlist: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _analyze_trend(self, ticker, days=14):
        """Analyze stock's short-term trend"""
        try:
            # Fetch data
            today = datetime.now()
            start_date = (today - timedelta(days=days)).strftime("%Y%m%d")
            end_date = today.strftime("%Y%m%d")

            from krx_data_client import get_market_ohlcv_by_date
            df = get_market_ohlcv_by_date(start_date, end_date, ticker)

            if df.empty:
                return 0  # Neutral (no data)

            # Calculate trend
            prices = df['Close'].values
            x = np.arange(len(prices))

            # Calculate trend using linear regression
            slope, _, _, _, _ = stats.linregress(x, prices)

            # Calculate trend strength relative to price change
            price_range = np.max(prices) - np.min(prices)
            normalized_slope = slope * len(prices) / price_range if price_range > 0 else 0

            # Determine trend based on threshold
            if normalized_slope > 0.15:  # Strong upward trend
                return 2
            elif normalized_slope > 0.05:  # Weak upward trend
                return 1
            elif normalized_slope < -0.15:  # Strong downward trend
                return -2
            elif normalized_slope < -0.05:  # Weak downward trend
                return -1
            else:  # Neutral trend
                return 0

        except Exception as e:
            logger.error(f"{ticker} Error analyzing trend: {str(e)}")
            return 0  # Assume neutral trend on error

    async def _analyze_sell_decision(self, stock_data):
        """AI agent-based sell decision analysis"""
        try:
            ticker = stock_data.get('ticker', '')
            company_name = stock_data.get('company_name', '')
            buy_price = stock_data.get('buy_price', 0)
            buy_date = stock_data.get('buy_date', '')
            current_price = stock_data.get('current_price', 0)
            target_price = stock_data.get('target_price', 0)
            stop_loss = stock_data.get('stop_loss', 0)

            # Calculate profit rate
            profit_rate = ((current_price - buy_price) / buy_price) * 100

            # Days elapsed from buy date
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            days_passed = (datetime.now() - buy_datetime).days

            # Extract scenario information
            scenario_str = stock_data.get('scenario', '{}')
            period = "중기"  # Default value
            sector = "알 수 없음"
            trading_scenarios = {}

            try:
                if isinstance(scenario_str, str):
                    scenario_data = json.loads(scenario_str)
                    period = scenario_data.get('investment_period', '중기')
                    sector = scenario_data.get('sector', '알 수 없음')
                    trading_scenarios = scenario_data.get('trading_scenarios', {})
            except:
                pass

            # Collect current portfolio information
            self.cursor.execute("""
                SELECT ticker, company_name, buy_price, current_price, scenario
                FROM stock_holdings
            """)
            holdings = [dict(row) for row in self.cursor.fetchall()]

            # Analyze sector distribution
            sector_distribution = {}
            investment_periods = {"단기": 0, "중기": 0, "장기": 0}

            for holding in holdings:
                holding_scenario_str = holding.get('scenario', '{}')
                try:
                    if isinstance(holding_scenario_str, str):
                        holding_scenario = json.loads(holding_scenario_str)
                    else:
                        holding_scenario = holding_scenario_str
                    # Collect sector information from each holding's scenario
                    holding_sector = holding_scenario.get('sector', '기타')
                    sector_distribution[holding_sector] = sector_distribution.get(holding_sector, 0) + 1
                    # Collect investment period information from each holding's scenario
                    holding_period = holding_scenario.get('investment_period', '중기')
                    investment_periods[holding_period] = investment_periods.get(holding_period, 0) + 1
                except:
                    # If parsing fails, use default values
                    sector_distribution['기타'] = sector_distribution.get('기타', 0) + 1
                    investment_periods['중기'] = investment_periods.get('중기', 0) + 1

            # Portfolio information string
            portfolio_info = f"""
            현재 Hold 종목 수: {len(holdings)}/{self.max_slots}
            산업군 분포: {json.dumps(sector_distribution, ensure_ascii=False)}
            투자 기간 분포: {json.dumps(investment_periods, ensure_ascii=False)}
            """

            # Log portfolio_info for debugging sell decision agent's sector analysis
            logger.info(f"[_analyze_sell_decision] {ticker}({company_name}) portfolio_info for sell decision:")
            logger.info(f"  - Holdings count: {len(holdings)}/{self.max_slots}")
            logger.info(f"  - Sector distribution: {json.dumps(sector_distribution, ensure_ascii=False)}")
            logger.info(f"  - Investment periods: {json.dumps(investment_periods, ensure_ascii=False)}")

            # LLM call to generate sell decision
            llm = await self.sell_decision_agent.attach_llm(OpenAIAugmentedLLM)

            # Prepare prompt based on language
            if self.language == "ko":
                prompt_message = f"""
                다음 Hold 종목에 대한 Sell 의사결정을 수행해주세요.

                ### 종목 기본 정보:
                - 종목명: {company_name}({ticker})
                - 매수가: {buy_price:,.0f}원
                - 현재가: {current_price:,.0f}원
                - 목표가: {target_price:,.0f} 원
                - 손절가: {stop_loss:,.0f}
                - 수익률: {profit_rate:.2f}%
                - Hold기간: {days_passed}일
                - 투자기간: {period}
                - 섹터: {sector}

                ### 현재 포트폴리오 상황:
                {portfolio_info}

                ### 매매 시나리오 정보:
                {json.dumps(trading_scenarios, ensure_ascii=False) if trading_scenarios else "시나리오 정보 없음"}

                ### 분석 요청:
                위 정보를 바탕으로 kospi_kosdaq과 sqlite 도구를 활용하여 최신 데이터를 확인하고,
                Sell할지 계속 Hold할지 결정해주세요.
                """
            else:  # English
                prompt_message = f"""
                Please make a sell decision for the following holding.

                ### Stock Basic Information:
                - Stock: {company_name}({ticker})
                - Buy Price: {buy_price:,.0f} KRW
                - Current Price: {current_price:,.0f} KRW
                - Target Price: {target_price:,.0f} KRW
                - Stop Loss: {stop_loss:,.0f} KRW
                - Return: {profit_rate:.2f}%
                - Holding Period: {days_passed} days
                - Investment Period: {period}
                - Sector: {sector}

                ### Current Portfolio Status:
                {portfolio_info}

                ### Trading Scenario Information:
                {json.dumps(trading_scenarios, ensure_ascii=False) if trading_scenarios else "No scenario information"}

                ### Analysis Request:
                Based on the above information, use the kospi_kosdaq and sqlite tools to check the latest data,
                and decide whether to sell or continue holding.
                """

            response = await llm.generate_str(
                message=prompt_message,
                request_params=RequestParams(
                    model="gpt-5.2",
                    maxTokens=16000
                )
            )

            # JSON 파싱
            try:
                # 응답이 비어있거나 None인 경우 조기 처리
                if not response or not response.strip():
                    logger.warning(f"{ticker} Empty response from LLM, falling back to legacy algorithm")
                    return await self._fallback_sell_decision(stock_data)
                
                decision_json = None
                json_str = None
                
                # 1. 마크다운 코드 블록에서 JSON 추출 시도 (```json ... ``` 또는 ``` ... ```)
                markdown_match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```', response, re.DOTALL)
                if markdown_match:
                    json_str = markdown_match.group(1)
                    logger.debug(f"Found JSON in markdown code block")
                
                # 2. 중첩된 중괄호를 포함한 완전한 JSON 객체 추출 시도
                if not json_str:
                    # 더 정교한 JSON 객체 매칭 (중첩된 {} 지원)
                    json_match = re.search(r'(\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\})', response, re.DOTALL)
                    if json_match:
                        json_str = json_match.group(1)
                        logger.debug(f"Found JSON object in response")
                
                # 3. 전체 응답이 JSON인 경우
                if not json_str:
                    # 앞뒤 공백 및 마크다운 제거 후 시도
                    clean_response = response.strip()
                    if clean_response.startswith('{') and clean_response.endswith('}'):
                        json_str = clean_response
                        logger.debug(f"Using entire response as JSON")
                
                # JSON 문자열이 없으면 폴백
                if not json_str:
                    logger.warning(f"{ticker} No JSON found in response (length: {len(response)}), falling back to legacy algorithm")
                    logger.debug(f"Response preview: {response[:500]}...")
                    return await self._fallback_sell_decision(stock_data)
                
                # JSON 전처리: trailing comma 제거
                json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
                
                # JSON 파싱 시도
                try:
                    decision_json = json.loads(json_str)
                except json.JSONDecodeError as e:
                    # 추가 정리 시도: 제어 문자 제거
                    json_str_cleaned = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', json_str)
                    decision_json = json.loads(json_str_cleaned)
                
                logger.info(f"Sell decision parse successful: {json.dumps(decision_json, ensure_ascii=False)[:500]}")

                # 결과 추출 - 기존 단일 형식 사용
                should_sell = decision_json.get("should_sell", False)
                sell_reason = decision_json.get("sell_reason", "AI 분석 결과")
                confidence = decision_json.get("confidence", 5)
                analysis_summary = decision_json.get("analysis_summary", {})
                portfolio_adjustment = decision_json.get("portfolio_adjustment", {})
                
                logger.info(f"{ticker}({company_name}) AI sell decision: {'Sell' if should_sell else 'Hold'} (Confidence: {confidence}/10)")
                logger.info(f"Sell reason: {sell_reason}")
                
                # ===== 핵심: should_sell 분기에 따른 DB 처리 (에러가 나도 메인 플로우는 계속 진행) =====
                try:
                    if should_sell:
                        # Sell 결정 시: holding_decisions 테이블에서 삭제
                        await self._delete_holding_decision(ticker)
                        
                        # Sell 시 analysis_summary를 sell_reason에 추가
                        if analysis_summary:
                            detailed_reason = self._format_sell_reason_with_analysis(sell_reason, analysis_summary)
                            return should_sell, detailed_reason
                    else:
                        # Hold 결정 시: holding_decisions 테이블에 저장/업데이트
                        await self._save_holding_decision(ticker, current_price, decision_json)
                        
                        # portfolio_adjustment 처리
                        if portfolio_adjustment.get("needed", False):
                            await self._process_portfolio_adjustment(ticker, company_name, portfolio_adjustment, analysis_summary)
                except Exception as db_err:
                    # DB 조작 실패해도 메인 플로우는 계속 진행
                    logger.error(f"{ticker} Error processing holding_decisions DB (main flow continues): {str(db_err)}")
                    logger.error(traceback.format_exc())
                
                return should_sell, sell_reason

            except Exception as json_err:
                logger.error(f"Sell decision JSON parse error: {json_err}")
                logger.error(f"Original response: {response}")
                
                # 파싱 실패 시 기존 알고리즘으로 폴백
                logger.warning(f"{ticker} AI analysis failed, falling back to legacy algorithm")
                return await self._fallback_sell_decision(stock_data)

        except Exception as e:
            logger.error(f"{stock_data.get('ticker', '') if 'ticker' in locals() else 'Unknown stock'} Error in AI sell analysis: {str(e)}")
            logger.error(traceback.format_exc())
            
            # 오류 시 기존 알고리즘으로 폴백
            return await self._fallback_sell_decision(stock_data)

    async def _fallback_sell_decision(self, stock_data):
        """Legacy algorithm-based sell decision (fallback)"""
        try:
            ticker = stock_data.get('ticker', '')
            buy_price = stock_data.get('buy_price', 0)
            buy_date = stock_data.get('buy_date', '')
            current_price = stock_data.get('current_price', 0)
            target_price = stock_data.get('target_price', 0)
            stop_loss = stock_data.get('stop_loss', 0)

            # Calculate profit rate
            profit_rate = ((current_price - buy_price) / buy_price) * 100

            # Days elapsed from buy date
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            days_passed = (datetime.now() - buy_datetime).days

            # Extract scenario information
            scenario_str = stock_data.get('scenario', '{}')
            investment_period = "중기"  # Default value

            try:
                if isinstance(scenario_str, str):
                    scenario_data = json.loads(scenario_str)
                    investment_period = scenario_data.get('investment_period', '중기')
            except:
                pass

            # Analyze stock trend (7-day linear regression)
            trend = await self._analyze_trend(ticker, days=7)

            # Check conditions according to sell decision priority

            # 1. Check stop-loss condition (highest priority)
            if stop_loss > 0 and current_price <= stop_loss:
                # Defer stop-loss in strong upward trend (exception case)
                if trend >= 2 and profit_rate > -7:  # Strong upward trend & loss < 7%
                    return False, "손절 유예 (강한 상승 추세)"
                return True, f"손절매 조건 도달 (손절가: {stop_loss:,.0f} 원)"

            # 2. Check target price reached
            if target_price > 0 and current_price >= target_price:
                # Continue holding if strong upward trend (exception case)
                if trend >= 2:
                    return False, "목표가 달성했으나 강한 상승 추세로 Hold 유지"
                return True, f"목표가 달성 (목표가: {target_price:,.0f} )"

            # 3. Sell conditions based on market state and trend (market environment consideration)
            if self.simple_market_condition == -1 and trend < 0 and profit_rate > 3:
                return True, f"약세장 + 하락 추세에서 수익 확보 (수익률: {profit_rate:.2f}%)"

            # 4. Conditions by investment period (differentiation by investment type)
            if investment_period == "단기":
                # Short-term investment profit target achieved
                if days_passed >= 15 and profit_rate >= 5 and trend < 2:
                    return True, f"단기 투자 목표 달성 (Hold일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

                # Short-term investment loss protection (but keep if strong upward trend)
                if days_passed >= 10 and profit_rate <= -3 and trend < 2:
                    return True, f"단기 투자 손실 방어 (Hold일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # 5. General profit target achieved (general investment not in specific period)
            if profit_rate >= 10 and trend < 2:
                return True, f"수익률 10% 이상 달성 (현재 수익률: {profit_rate:.2f}%)"

            # 6. Status check after long-term holding (decision based on time elapsed)
            # Case where above stop-loss but loss persists long-term
            if days_passed >= 30 and profit_rate < 0 and trend < 1:
                return True, f"30일 이상 Hold 중이며 손실 상태 (Hold일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            if days_passed >= 60 and profit_rate >= 3 and trend < 1:
                return True, f"60일 이상 Hold 중이며 3% 이상 수익 (Hold일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # 7. Long-term check by investment type (investment period specialization)
            if investment_period == "장기" and days_passed >= 90 and profit_rate < 0 and trend < 1:
                return True, f"장기 투자 손실 정리 (Hold일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # 8. Not stop-loss but severe loss occurred (emergency response)
            # General loss sell condition applies only when not below stop-loss
            # Case where stop-loss not set (0) or current price above stop-loss with large loss (-5%+)
            if (stop_loss == 0 or current_price > stop_loss) and profit_rate <= -5 and trend < 1:
                return True, f"심각한 손실 발생 (현재 수익률: {profit_rate:.2f}%)"

            # Continue holding by default
            trend_text = {
                2: "강한 상승 추세", 1: "약한 상승 추세", 0: "중립 추세",
                -1: "약한 하락 추세", -2: "강한 하락 추세"
            }.get(trend, "알 수 없는 추세")

            return False, f"계속 Hold (추세: {trend_text}, 수익률: {profit_rate:.2f}%)"

        except Exception as e:
            logger.error(f"Error in fallback sell analysis: {str(e)}")
            return False, "분석 오류"

    async def _process_portfolio_adjustment(self, ticker: str, company_name: str, portfolio_adjustment: Dict[str, Any], analysis_summary: Dict[str, Any]):
        """Process DB updates and Telegram notifications based on portfolio_adjustment"""
        try:
            # Return if adjustment not needed
            if not portfolio_adjustment.get("needed", False):
                return

            # Check urgency - if low, only log without actual update
            urgency = portfolio_adjustment.get("urgency", "low").lower()
            if urgency == "low":
                logger.info(f"{ticker} Portfolio adjustment suggestion (urgency=low): {portfolio_adjustment.get('reason', '')}")
                return

            db_updated = False
            update_message = ""
            adjustment_reason = portfolio_adjustment.get("reason", "AI 분석 결과")

            # Adjust target price
            new_target_price = portfolio_adjustment.get("new_target_price")
            if new_target_price is not None:
                # Safe number conversion (including comma removal)
                target_price_num = self._safe_number_conversion(new_target_price)
                if target_price_num > 0:
                    self.cursor.execute(
                        "UPDATE stock_holdings SET target_price = ? WHERE ticker = ?",
                        (target_price_num, ticker)
                    )
                    self.conn.commit()
                    db_updated = True
                    update_message += f"목표가: {target_price_num:,.0f} 원으로 조정\n"
                    logger.info(f"{ticker} Target price AI adjustment: {target_price_num:,.0f} KRW (Urgency: {urgency})")

            # Adjust stop-loss
            new_stop_loss = portfolio_adjustment.get("new_stop_loss")
            if new_stop_loss is not None:
                # Safe number conversion (including comma removal)
                stop_loss_num = self._safe_number_conversion(new_stop_loss)
                if stop_loss_num > 0:
                    self.cursor.execute(
                        "UPDATE stock_holdings SET stop_loss = ? WHERE ticker = ?",
                        (stop_loss_num, ticker)
                    )
                    self.conn.commit()
                    db_updated = True
                    update_message += f"손절가: {stop_loss_num:,.0f} 으로 조정\n"
                    logger.info(f"{ticker} Stop-loss AI adjustment: {stop_loss_num:,.0f} 원 (Urgency: {urgency})")

            # Generate Telegram message if DB was updated
            if db_updated:
                urgency_emoji = {"high": "🚨", "medium": "⚠️", "low": "💡"}.get(urgency, "🔄")
                message = f"{urgency_emoji} 포트폴리오 조정: {company_name}({ticker})\n"
                message += update_message
                message += f"조정 근거: {adjustment_reason}\n"
                message += f"Urgency: {urgency.upper()}\n"

                # Add analysis summary
                if analysis_summary:
                    message += f"기술적 추세: {analysis_summary.get('technical_trend', 'N/A')}\n"
                    message += f"시장 환경 영향: {analysis_summary.get('market_condition_impact', 'N/A')}"

                self.message_queue.append(message)
                logger.info(f"{ticker} AI-based portfolio adjustment complete: {update_message.strip()}")
            else:
                # Case where adjustment was requested but no specific values provided
                logger.warning(f"{ticker} Portfolio adjustment requested but no specific values: {portfolio_adjustment}")
            
        except Exception as e:
            logger.error(f"{ticker} Error processing portfolio adjustment: {str(e)}")
            logger.error(traceback.format_exc())

    def _safe_number_conversion(self, value) -> float:
        """다양한 형태의 값을 안전하게 숫자로 변환"""
        try:
            # 이미 숫자 타입인 경우
            if isinstance(value, (int, float)):
                return float(value)
            
            # 문자열인 경우
            if isinstance(value, str):
                # 쉼표 제거하고 공백 제거
                cleaned_value = value.replace(',', '').replace(' ', '')
                # "원" 제거 (혹시 포함되어 있을 경우)
                cleaned_value = cleaned_value.replace('원', '')
                
                # 빈 문자열 체크
                if not cleaned_value:
                    return 0.0
                
                # 숫자로 변환
                return float(cleaned_value)
            
            # null이나 기타 타입인 경우
            return 0.0
            
        except (ValueError, TypeError) as e:
            logger.warning(f"Number conversion failed: {value} -> {str(e)}")
            return 0.0

    async def _save_holding_decision(self, ticker: str, current_price: float, decision_json: Dict[str, Any]) -> bool:
        """
        Save AI sell decision results for held stocks to holding_decisions table
        (Main flow continues even if fails)

        Args:
            ticker: Stock ticker
            current_price: Current price
            decision_json: AI decision result JSON

        Returns:
            bool: Save success status
        """
        try:
            now = datetime.now()
            decision_date = now.strftime("%Y-%m-%d")
            decision_time = now.strftime("%H:%M:%S")

            # Extract data from JSON
            should_sell = decision_json.get("should_sell", False)
            sell_reason = decision_json.get("sell_reason", "")
            confidence = decision_json.get("confidence", 0)

            analysis_summary = decision_json.get("analysis_summary", {})
            technical_trend = analysis_summary.get("technical_trend", "")
            volume_analysis = analysis_summary.get("volume_analysis", "")
            market_condition_impact = analysis_summary.get("market_condition_impact", "")
            time_factor = analysis_summary.get("time_factor", "")

            portfolio_adjustment = decision_json.get("portfolio_adjustment", {})
            adjustment_needed = portfolio_adjustment.get("needed", False)
            adjustment_reason = portfolio_adjustment.get("reason", "")
            new_target_price = self._safe_number_conversion(portfolio_adjustment.get("new_target_price"))
            new_stop_loss = self._safe_number_conversion(portfolio_adjustment.get("new_stop_loss"))
            adjustment_urgency = portfolio_adjustment.get("urgency", "low")

            # Save full JSON as string
            full_json_data = json.dumps(decision_json, ensure_ascii=False)

            # Delete existing data then insert new (keep only latest decision for same ticker)
            self.cursor.execute("DELETE FROM holding_decisions WHERE ticker = ?", (ticker,))

            # Insert new decision
            self.cursor.execute("""
                INSERT INTO holding_decisions (
                    ticker, decision_date, decision_time, current_price, should_sell,
                    sell_reason, confidence, technical_trend, volume_analysis,
                    market_condition_impact, time_factor, portfolio_adjustment_needed,
                    adjustment_reason, new_target_price, new_stop_loss, adjustment_urgency,
                    full_json_data
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ticker, decision_date, decision_time, current_price, should_sell,
                sell_reason, confidence, technical_trend, volume_analysis,
                market_condition_impact, time_factor, adjustment_needed,
                adjustment_reason, new_target_price, new_stop_loss, adjustment_urgency,
                full_json_data
            ))

            self.conn.commit()
            logger.info(f"{ticker} Hold decision save complete - should_sell: {should_sell}, confidence: {confidence}")
            return True

        except Exception as e:
            logger.error(f"{ticker} Hold decision save failed (main flow continues): {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _delete_holding_decision(self, ticker: str) -> bool:
        """
        Delete decision data for sold stocks from holding_decisions table
        (Main flow continues even if fails)

        Args:
            ticker: Stock ticker

        Returns:
            bool: Delete success status
        """
        try:
            self.cursor.execute("DELETE FROM holding_decisions WHERE ticker = ?", (ticker,))
            self.conn.commit()
            logger.info(f"{ticker} Sell decision data deleted")
            return True
            
        except Exception as e:
            logger.error(f"{ticker} Sell decision delete failed (main flow continues): {str(e)}")
            return False

    def _format_sell_reason_with_analysis(self, sell_reason: str, analysis_summary: Dict[str, Any]) -> str:
        """Sell 이유에 분석 요약 추가"""
        try:
            detailed_reason = sell_reason
            
            if analysis_summary:
                detailed_reason += "\n\n📊 상세 분석:"
                
                if analysis_summary.get('technical_trend'):
                    detailed_reason += f"\n• 기술적 추세: {analysis_summary['technical_trend']}"
                
                if analysis_summary.get('volume_analysis'):
                    detailed_reason += f"\n• 거래량 분석: {analysis_summary['volume_analysis']}"
                
                if analysis_summary.get('market_condition_impact'):
                    detailed_reason += f"\n• 시장 환경: {analysis_summary['market_condition_impact']}"
                
                if analysis_summary.get('time_factor'):
                    detailed_reason += f"\n• 시간 요인: {analysis_summary['time_factor']}"
            
            return detailed_reason
            
        except Exception as e:
            logger.error(f"Error formatting sell reason: {str(e)}")
            return sell_reason