#!/usr/bin/env python3
"""
주식 포트폴리오 대시보드용 JSON 데이터 생성 스크립트
Cron으로 주기적 실행 (예: */5 * * * * - 5분마다)

Usage:
    python generate_dashboard_json.py

Output:
    ./dashboard/public/dashboard_data.json - 대시보드에서 사용할 모든 데이터
"""

import sqlite3
import json
import sys
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any
import logging

# 경로 설정
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent
TRADING_DIR = PROJECT_ROOT / "trading"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(TRADING_DIR))

# 설정파일 로딩
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
try:
    with open(CONFIG_FILE, encoding="UTF-8") as f:
        _cfg = yaml.load(f, Loader=yaml.FullLoader)
except FileNotFoundError:
    _cfg = {"default_mode": "demo"}
    logging.warning(f"설정 파일을 찾을 수 없습니다: {CONFIG_FILE}. 기본 모드(demo)를 사용합니다.")

# 한국투자증권 API 모듈 import
try:
    from trading.domestic_stock_trading import DomesticStockTrading
    KIS_AVAILABLE = True
except ImportError:
    KIS_AVAILABLE = False
    logging.warning("한국투자증권 API 모듈을 찾을 수 없습니다. 실전투자 데이터를 가져올 수 없습니다.")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class DashboardDataGenerator:
    def __init__(self, db_path: str = None, output_path: str = None, trading_mode: str = None):
        # db_path 기본값: 프로젝트 루트의 stock_tracking_db.sqlite
        if db_path is None:
            db_path = str(PROJECT_ROOT / "stock_tracking_db.sqlite")
        
        # output_path 기본값: examples/dashboard/public/dashboard_data.json
        if output_path is None:
            output_path = str(SCRIPT_DIR / "dashboard" / "public" / "dashboard_data.json")
        
        self.db_path = db_path
        self.output_path = output_path
        self.trading_mode = trading_mode if trading_mode is not None else _cfg.get("default_mode", "demo")
        
    def connect_db(self):
        """DB 연결"""
        return sqlite3.connect(self.db_path)
    
    def get_kis_trading_data(self) -> Dict[str, Any]:
        """한국투자증권 API로부터 실전투자 데이터 가져오기"""
        if not KIS_AVAILABLE:
            logger.warning("한국투자증권 API를 사용할 수 없습니다.")
            return {"portfolio": [], "account_summary": {}}
        
        try:
            logger.info(f"한국투자증권 데이터 조회 중... (모드: {self.trading_mode})")
            trader = DomesticStockTrading(mode=self.trading_mode)
            
            # 포트폴리오 데이터 조회
            portfolio = trader.get_portfolio()
            logger.info(f"포트폴리오 조회 완료: {len(portfolio)}개 종목")
            
            # 계좌 요약 데이터 조회
            account_summary = trader.get_account_summary()
            logger.info("계좌 요약 조회 완료")
            
            # 데이터 변환 (dashboard 형식에 맞게)
            formatted_portfolio = []
            for stock in portfolio:
                formatted_stock = {
                    "ticker": stock.get("stock_code", ""),
                    "name": stock.get("stock_name", ""),
                    "quantity": stock.get("quantity", 0),
                    "avg_price": stock.get("avg_price", 0),
                    "current_price": stock.get("current_price", 0),
                    "value": stock.get("eval_amount", 0),
                    "profit": stock.get("profit_amount", 0),
                    "profit_rate": stock.get("profit_rate", 0),
                    "sector": "실전투자",  # 섹터 정보가 없으면 기본값
                    "weight": 0  # 나중에 계산
                }
                formatted_portfolio.append(formatted_stock)
            
            # 포트폴리오 비중 계산
            total_value = sum(s["value"] for s in formatted_portfolio)
            if total_value > 0:
                for stock in formatted_portfolio:
                    stock["weight"] = (stock["value"] / total_value) * 100
            
            return {
                "portfolio": formatted_portfolio,
                "account_summary": account_summary
            }
            
        except Exception as e:
            logger.error(f"한국투자증권 데이터 조회 중 오류: {str(e)}")
            return {"portfolio": [], "account_summary": {}}
        
    def connect_db(self):
        """DB 연결"""
        return sqlite3.connect(self.db_path)
    
    def parse_json_field(self, json_str: str) -> Dict:
        """JSON 문자열 파싱 (에러 처리 포함)"""
        if not json_str:
            return {}
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON 파싱 실패: {str(e)}")
            return {}
    
    def dict_from_row(self, row, cursor) -> Dict:
        """SQLite Row를 Dictionary로 변환"""
        return {col[0]: row[idx] for idx, col in enumerate(cursor.description)}
    
    def get_stock_holdings(self, conn) -> List[Dict]:
        """현재 보유 종목 데이터 가져오기"""
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ticker, company_name, buy_price, buy_date, current_price, 
                   last_updated, scenario, target_price, stop_loss
            FROM stock_holdings
            ORDER BY buy_date DESC
        """)
        
        holdings = []
        for row in cursor.fetchall():
            holding = self.dict_from_row(row, cursor)
            
            # scenario JSON 파싱
            holding['scenario'] = self.parse_json_field(holding.get('scenario', ''))
            
            # 수익률 계산
            buy_price = holding.get('buy_price', 0)
            current_price = holding.get('current_price', 0)
            if buy_price > 0:
                holding['profit_rate'] = ((current_price - buy_price) / buy_price) * 100
            else:
                holding['profit_rate'] = 0
            
            # 투자 기간 계산
            buy_date = holding.get('buy_date', '')
            if buy_date:
                try:
                    buy_dt = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
                    holding['holding_days'] = (datetime.now() - buy_dt).days
                except:
                    holding['holding_days'] = 0
            else:
                holding['holding_days'] = 0
            
            holdings.append(holding)
        
        return holdings
    
    def get_trading_history(self, conn) -> List[Dict]:
        """거래 이력 데이터 가져오기"""
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, ticker, company_name, buy_price, buy_date, sell_price, 
                   sell_date, profit_rate, holding_days, scenario
            FROM trading_history
            ORDER BY sell_date DESC
        """)
        
        history = []
        for row in cursor.fetchall():
            trade = self.dict_from_row(row, cursor)
            
            # scenario JSON 파싱
            trade['scenario'] = self.parse_json_field(trade.get('scenario', ''))
            
            history.append(trade)
        
        return history
    
    def get_watchlist_history(self, conn) -> List[Dict]:
        """관망 종목 데이터 가져오기"""
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, ticker, company_name, current_price, analyzed_date, 
                   buy_score, min_score, decision, skip_reason, target_price, 
                   stop_loss, investment_period, sector, scenario, 
                   portfolio_analysis, valuation_analysis, sector_outlook, 
                   market_condition, rationale
            FROM watchlist_history
            ORDER BY analyzed_date DESC
        """)
        
        watchlist = []
        for row in cursor.fetchall():
            item = self.dict_from_row(row, cursor)
            
            # scenario JSON 파싱
            item['scenario'] = self.parse_json_field(item.get('scenario', ''))
            
            watchlist.append(item)
        
        return watchlist
    
    def get_market_condition(self, conn) -> List[Dict]:
        """시장 상황 데이터 가져오기"""
        cursor = conn.cursor()
        cursor.execute("""
            SELECT date, kospi_index, kosdaq_index, condition, volatility
            FROM market_condition
            ORDER BY date DESC
            LIMIT 30
        """)
        
        market_data = []
        for row in cursor.fetchall():
            market = self.dict_from_row(row, cursor)
            market_data.append(market)
        
        return market_data
    
    def get_holding_decisions(self, conn) -> List[Dict]:
        """보유 종목 매도 판단 데이터 가져오기"""
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, ticker, decision_date, decision_time, current_price, 
                       should_sell, sell_reason, confidence, technical_trend, 
                       volume_analysis, market_condition_impact, time_factor, 
                       portfolio_adjustment_needed, adjustment_reason, 
                       new_target_price, new_stop_loss, adjustment_urgency, 
                       full_json_data, created_at
                FROM holding_decisions
                ORDER BY created_at DESC
            """)
            
            decisions = []
            for row in cursor.fetchall():
                decision = self.dict_from_row(row, cursor)
                
                # full_json_data 파싱
                decision['full_json_data'] = self.parse_json_field(decision.get('full_json_data', ''))
                
                decisions.append(decision)
            
            return decisions
        except Exception as e:
            logger.warning(f"holding_decisions 테이블 조회 실패 (테이블이 없을 수 있음): {str(e)}")
            return []
    
    def calculate_portfolio_summary(self, holdings: List[Dict]) -> Dict:
        """포트폴리오 요약 통계 계산"""
        if not holdings:
            return {
                'total_stocks': 0,
                'total_profit': 0,
                'avg_profit_rate': 0,
                'slot_usage': '0/10',
                'slot_percentage': 0
            }
        
        total_profit = sum(h.get('profit_rate', 0) for h in holdings)
        avg_profit_rate = total_profit / len(holdings) if holdings else 0
        
        # 섹터별 분포
        sector_distribution = {}
        for h in holdings:
            scenario = h.get('scenario', {})
            sector = scenario.get('sector', '기타')
            sector_distribution[sector] = sector_distribution.get(sector, 0) + 1
        
        # 투자기간별 분포
        period_distribution = {}
        for h in holdings:
            scenario = h.get('scenario', {})
            period = scenario.get('investment_period', '단기')
            period_distribution[period] = period_distribution.get(period, 0) + 1
        
        return {
            'total_stocks': len(holdings),
            'total_profit': total_profit,
            'avg_profit_rate': avg_profit_rate,
            'slot_usage': f'{len(holdings)}/10',
            'slot_percentage': (len(holdings) / 10) * 100,
            'sector_distribution': sector_distribution,
            'period_distribution': period_distribution
        }
    
    def calculate_trading_summary(self, history: List[Dict]) -> Dict:
        """거래 이력 요약 통계 계산"""
        if not history:
            return {
                'total_trades': 0,
                'win_count': 0,
                'loss_count': 0,
                'win_rate': 0,
                'avg_profit_rate': 0,
                'avg_holding_days': 0
            }
        
        win_count = sum(1 for h in history if h.get('profit_rate', 0) > 0)
        loss_count = len(history) - win_count
        win_rate = (win_count / len(history)) * 100 if history else 0
        
        avg_profit_rate = sum(h.get('profit_rate', 0) for h in history) / len(history)
        avg_holding_days = sum(h.get('holding_days', 0) for h in history) / len(history)
        
        return {
            'total_trades': len(history),
            'win_count': win_count,
            'loss_count': loss_count,
            'win_rate': win_rate,
            'avg_profit_rate': avg_profit_rate,
            'avg_holding_days': avg_holding_days
        }
    
    def get_ai_decision_summary(self, decisions: List[Dict]) -> Dict:
        """AI 판단 요약 통계"""
        if not decisions:
            return {
                'total_decisions': 0,
                'sell_signals': 0,
                'hold_signals': 0,
                'adjustment_needed': 0,
                'avg_confidence': 0
            }
        
        sell_signals = sum(1 for d in decisions if d.get('should_sell', False))
        hold_signals = len(decisions) - sell_signals
        adjustment_needed = sum(1 for d in decisions if d.get('portfolio_adjustment_needed', False))
        
        avg_confidence = sum(d.get('confidence', 0) for d in decisions) / len(decisions) if decisions else 0
        
        return {
            'total_decisions': len(decisions),
            'sell_signals': sell_signals,
            'hold_signals': hold_signals,
            'adjustment_needed': adjustment_needed,
            'avg_confidence': avg_confidence
        }
    
    def calculate_real_trading_summary(self, real_portfolio: List[Dict], account_summary: Dict) -> Dict:
        """실전투자 요약 통계 계산"""
        if not real_portfolio and not account_summary:
            return {
                'total_stocks': 0,
                'total_eval_amount': 0,
                'total_profit_amount': 0,
                'total_profit_rate': 0,
                'available_amount': 0
            }
        
        return {
            'total_stocks': len(real_portfolio),
            'total_eval_amount': account_summary.get('total_eval_amount', 0),
            'total_profit_amount': account_summary.get('total_profit_amount', 0),
            'total_profit_rate': account_summary.get('total_profit_rate', 0),
            'available_amount': account_summary.get('available_amount', 0)
        }
    
    def get_operating_costs(self) -> Dict:
        """프로젝트 운영 비용 데이터 반환"""
        # 2025년 10월 기준 운영 비용
        return {
            'server_hosting': 31.68,
            'openai_api': 95.82,
            'anthropic_api': 18.2,
            'firecrawl_api': 19.0,
            'perplexity_api': 9.9,
            'month': '2025-10'
        }
    
    def generate(self) -> Dict:
        """전체 대시보드 데이터 생성"""
        try:
            logger.info(f"DB 연결 중: {self.db_path}")
            conn = self.connect_db()
            conn.row_factory = sqlite3.Row
            
            logger.info("데이터 수집 시작...")
            
            # 각 테이블 데이터 수집
            holdings = self.get_stock_holdings(conn)
            trading_history = self.get_trading_history(conn)
            watchlist = self.get_watchlist_history(conn)
            market_condition = self.get_market_condition(conn)
            holding_decisions = self.get_holding_decisions(conn)
            
            # 한국투자증권 실전투자 데이터 수집
            kis_data = self.get_kis_trading_data()
            real_portfolio = kis_data.get("portfolio", [])
            account_summary = kis_data.get("account_summary", {})
            
            # 요약 통계 계산
            portfolio_summary = self.calculate_portfolio_summary(holdings)
            trading_summary = self.calculate_trading_summary(trading_history)
            ai_decision_summary = self.get_ai_decision_summary(holding_decisions)
            
            # 실전투자 요약 계산
            real_trading_summary = self.calculate_real_trading_summary(real_portfolio, account_summary)
            
            # 전체 데이터 구성
            dashboard_data = {
                'generated_at': datetime.now().isoformat(),
                'trading_mode': self.trading_mode,
                'summary': {
                    'portfolio': portfolio_summary,
                    'trading': trading_summary,
                    'ai_decisions': ai_decision_summary,
                    'real_trading': real_trading_summary
                },
                'holdings': holdings,
                'real_portfolio': real_portfolio,  # 실전투자 포트폴리오 추가
                'account_summary': account_summary,  # 계좌 요약 추가
                'operating_costs': self.get_operating_costs(),  # 운영 비용 추가
                'trading_history': trading_history,
                'watchlist': watchlist,
                'market_condition': market_condition,
                'holding_decisions': holding_decisions
            }
            
            conn.close()
            
            logger.info(f"데이터 수집 완료: 보유 {len(holdings)}개, 실전 {len(real_portfolio)}개, 거래 {len(trading_history)}건, 관망 {len(watchlist)}개")
            
            return dashboard_data
            
        except Exception as e:
            logger.error(f"데이터 생성 중 오류: {str(e)}")
            raise
    
    def save(self, data: Dict):
        """JSON 파일로 저장"""
        try:
            output_file = Path(self.output_path)
            
            # 디렉토리가 없으면 생성
            output_file.parent.mkdir(parents=True, exist_ok=True)
            
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            file_size = output_file.stat().st_size
            logger.info(f"JSON 파일 저장 완료: {output_file} ({file_size:,} bytes)")
            
        except Exception as e:
            logger.error(f"파일 저장 중 오류: {str(e)}")
            raise


def main():
    """메인 실행 함수"""
    import argparse
    
    parser = argparse.ArgumentParser(description="대시보드 JSON 생성")
    parser.add_argument("--mode", choices=["demo", "real"], 
                       help=f"트레이딩 모드 (demo: 모의투자, real: 실전투자, 기본값: {_cfg.get('default_mode', 'demo')})")
    
    args = parser.parse_args()
    
    try:
        logger.info("=== 대시보드 JSON 생성 시작 ===")
        
        generator = DashboardDataGenerator(trading_mode=args.mode)
        
        # 데이터 생성
        dashboard_data = generator.generate()
        
        # JSON 파일 저장
        generator.save(dashboard_data)
        
        logger.info("=== 대시보드 JSON 생성 완료 ===")
        
    except Exception as e:
        logger.error(f"실행 중 오류 발생: {str(e)}")
        exit(1)


if __name__ == "__main__":
    main()
