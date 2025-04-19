import numpy as np
from scipy import stats
from typing import List, Tuple, Dict, Any
from datetime import datetime, timedelta
from stock_tracking_agent import StockTrackingAgent
import logging
import json
import traceback

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
    """개선된 주식 트래킹 및 매매 에이전트"""

    def __init__(self, db_path: str = "stock_tracking_db.sqlite", telegram_token: str = None):
        """에이전트 초기화"""
        super().__init__(db_path, telegram_token)
        # 시장 상태 저장 변수 (1: 강세장, 0: 중립, -1: 약세장)
        self.market_condition = 0
        # 변동성 테이블 (종목별 변동성 저장)
        self.volatility_table = {}

    async def initialize(self):
        """필요한 테이블 생성 및 초기화"""
        await super().initialize()

        # 시장 상태 분석 테이블 생성
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS market_condition (
                date TEXT PRIMARY KEY,
                kospi_index REAL,
                kosdaq_index REAL,
                condition INTEGER,  -- 1: 강세장, 0: 중립, -1: 약세장
                volatility REAL
            )
        """)

        self.conn.commit()

        # 시장 상태 분석 실행
        await self._analyze_market_condition()

        return True

    async def _analyze_market_condition(self):
        """시장 상태 분석 (강세장/약세장)"""
        try:
            from pykrx.stock import stock_api
            import datetime as dt

            # 오늘 날짜
            today = dt.datetime.now().strftime("%Y%m%d")

            # 1달 전 날짜
            one_month_ago = (dt.datetime.now() - dt.timedelta(days=30)).strftime("%Y%m%d")

            # 코스피, 코스닥 지수 데이터 가져오기
            kospi_df = stock_api.get_index_ohlcv_by_date(one_month_ago, today, "1001")
            kosdaq_df = stock_api.get_index_ohlcv_by_date(one_month_ago, today, "2001")

            # 지수 추세 분석
            kospi_trend = self._calculate_trend(kospi_df['종가'])
            kosdaq_trend = self._calculate_trend(kosdaq_df['종가'])

            # 전체 시장 상태 결정
            # 두 지수 모두 상승 추세면 강세장(1), 두 지수 모두 하락 추세면 약세장(-1), 그 외는 중립(0)
            if kospi_trend > 0 and kosdaq_trend > 0:
                market_condition = 1  # 강세장
            elif kospi_trend < 0 and kosdaq_trend < 0:
                market_condition = -1  # 약세장
            else:
                market_condition = 0  # 중립

            # 시장 변동성 계산 (코스피, 코스닥 변동성의 평균)
            kospi_volatility = self._calculate_volatility(kospi_df['종가'])
            kosdaq_volatility = self._calculate_volatility(kosdaq_df['종가'])
            avg_volatility = (kospi_volatility + kosdaq_volatility) / 2

            # 시장 상태 저장
            self.market_condition = market_condition

            # DB에 저장
            current_date = dt.datetime.now().strftime("%Y-%m-%d")
            self.cursor.execute(
                """
                INSERT OR REPLACE INTO market_condition 
                (date, kospi_index, kosdaq_index, condition, volatility)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    current_date,
                    kospi_df['종가'].iloc[-1],
                    kosdaq_df['종가'].iloc[-1],
                    market_condition,
                    avg_volatility
                )
            )
            self.conn.commit()

            logger.info(f"시장 상태 분석 완료: {'강세장' if market_condition == 1 else '약세장' if market_condition == -1 else '중립'}, 변동성: {avg_volatility:.2f}%")

            return market_condition, avg_volatility

        except Exception as e:
            logger.error(f"시장 상태 분석 중 오류: {str(e)}")
            return 0, 0  # 오류 시 중립 상태로 가정

    def _calculate_trend(self, price_series):
        """가격 시리즈의 추세 분석 (양수: 상승, 음수: 하락)"""
        # 단순 선형 회귀로 추세 계산
        x = np.arange(len(price_series))
        slope, _, _, _, _ = stats.linregress(x, price_series)
        return slope

    def _calculate_volatility(self, price_series):
        """가격 시리즈의 변동성 계산 (일간 수익률의 표준편차, 연율화)"""
        daily_returns = price_series.pct_change().dropna()
        daily_volatility = daily_returns.std()
        return daily_volatility * 100  # 퍼센트로 변환

    async def _get_stock_volatility(self, ticker):
        """개별 종목의 변동성 계산"""
        try:
            # 캐시된 변동성이 있으면 사용
            if ticker in self.volatility_table:
                return self.volatility_table[ticker]

            # 60일간의 가격 데이터 가져오기
            today = datetime.now()
            start_date = (today - timedelta(days=60)).strftime("%Y%m%d")
            end_date = today.strftime("%Y%m%d")

            # pykrx 사용하여 주가 데이터 가져오기
            from pykrx.stock import stock_api
            df = stock_api.get_market_ohlcv_by_date(start_date, end_date, ticker)

            if df.empty:
                logger.warning(f"{ticker} 가격 데이터를 가져올 수 없습니다.")
                return 15.0  # 기본 변동성 (15%)

            # 일간 수익률의 표준편차 계산
            daily_returns = df['종가'].pct_change().dropna()
            volatility = daily_returns.std() * 100  # 퍼센트로 변환

            # 변동성 테이블에 저장
            self.volatility_table[ticker] = volatility

            return volatility

        except Exception as e:
            logger.error(f"{ticker} 변동성 계산 중 오류: {str(e)}")
            return 15.0  # 오류 시 기본 변동성 반환

    async def _dynamic_stop_loss(self, ticker, buy_price):
        """종목별 변동성에 기반한 동적 손절 가격 계산"""
        try:
            # 종목의 변동성 가져오기
            volatility = await self._get_stock_volatility(ticker)

            # 변동성에 따른 손절폭 계산 (변동성이 클수록 더 넓게 설정)
            # 기본 손절폭 5%에 변동성 조정치 적용
            base_stop_loss_pct = 5.0

            # 시장 평균 변동성 (15% 가정) 대비 상대적 변동성 비율
            relative_volatility = volatility / 15.0

            # 조정된 손절폭 계산 (최소 3%, 최대 15%)
            adjusted_stop_loss_pct = min(max(base_stop_loss_pct * relative_volatility, 3.0), 15.0)

            # 시장 상태에 따른 추가 조정
            if self.market_condition == -1:  # 약세장
                adjusted_stop_loss_pct = adjusted_stop_loss_pct * 0.8  # 더 타이트하게
            elif self.market_condition == 1:  # 강세장
                adjusted_stop_loss_pct = adjusted_stop_loss_pct * 1.2  # 더 넓게

            # 손절가 계산
            stop_loss = buy_price * (1 - adjusted_stop_loss_pct/100)

            logger.info(f"{ticker} 동적 손절가 계산: {stop_loss:,.0f}원 (변동성: {volatility:.2f}%, 손절폭: {adjusted_stop_loss_pct:.2f}%)")

            return stop_loss

        except Exception as e:
            logger.error(f"{ticker} 동적 손절가 계산 중 오류: {str(e)}")
            return buy_price * 0.95  # 오류 시 기본 5% 손절폭 적용

    async def _dynamic_target_price(self, ticker, buy_price):
        """종목별 변동성에 기반한 동적 목표가 계산"""
        try:
            # 종목의 변동성 가져오기
            volatility = await self._get_stock_volatility(ticker)

            # 변동성에 따른 목표가 계산 (변동성이 클수록 더 높게 설정)
            # 기본 목표 수익률 10%에 변동성 조정치 적용
            base_target_pct = 10.0

            # 시장 평균 변동성 (15% 가정) 대비 상대적 변동성 비율
            relative_volatility = volatility / 15.0

            # 조정된 목표 수익률 계산 (최소 5%, 최대 30%)
            adjusted_target_pct = min(max(base_target_pct * relative_volatility, 5.0), 30.0)

            # 시장 상태에 따른 추가 조정
            if self.market_condition == 1:  # 강세장
                adjusted_target_pct = adjusted_target_pct * 1.3  # 더 높게
            elif self.market_condition == -1:  # 약세장
                adjusted_target_pct = adjusted_target_pct * 0.7  # 더 낮게

            # 목표가 계산
            target_price = buy_price * (1 + adjusted_target_pct/100)

            logger.info(f"{ticker} 동적 목표가 계산: {target_price:,.0f}원 (변동성: {volatility:.2f}%, 목표 수익률: {adjusted_target_pct:.2f}%)")

            return target_price

        except Exception as e:
            logger.error(f"{ticker} 동적 목표가 계산 중 오류: {str(e)}")
            return buy_price * 1.1  # 오류 시 기본 10% 목표 수익률 적용

    async def process_reports(self, pdf_report_paths: List[str]) -> Tuple[int, int]:
        """
        분석 보고서를 처리하여 매매 의사결정 수행

        Args:
            pdf_report_paths: pdf 분석 보고서 파일 경로 리스트

        Returns:
            Tuple[int, int]: 매수 건수, 매도 건수
        """
        try:
            logger.info(f"총 {len(pdf_report_paths)}개 보고서 처리 시작")

            # 매수, 매도 카운터
            buy_count = 0
            sell_count = 0

            # 1. 기존 보유 종목 업데이트 및 매도 의사결정
            sold_stocks = await self.update_holdings()
            sell_count = len(sold_stocks)

            if sold_stocks:
                logger.info(f"{len(sold_stocks)}개 종목 매도 완료")
                for stock in sold_stocks:
                    logger.info(f"매도: {stock['company_name']}({stock['ticker']}) - 수익률: {stock['profit_rate']:.2f}% / 이유: {stock['reason']}")
            else:
                logger.info("매도된 종목이 없습니다.")

            # 2. 새로운 보고서 분석 및 매수 의사결정
            for pdf_report_path in pdf_report_paths:
                # 보고서 분석
                analysis_result = await self.analyze_report(pdf_report_path)

                if not analysis_result.get("success", False):
                    logger.error(f"보고서 분석 실패: {pdf_report_path} - {analysis_result.get('error', '알 수 없는 오류')}")
                    continue

                # 이미 보유 중인 종목이면 스킵
                if analysis_result.get("decision") == "보유 중":
                    logger.info(f"보유 중 종목 스킵: {analysis_result.get('ticker')} - {analysis_result.get('company_name')}")
                    continue

                # 종목 정보 및 시나리오
                ticker = analysis_result.get("ticker")
                company_name = analysis_result.get("company_name")
                current_price = analysis_result.get("current_price", 0)
                scenario = analysis_result.get("scenario", {})
                sector = analysis_result.get("sector", "알 수 없음")
                sector_diverse = analysis_result.get("sector_diverse", True)
                rank_change_percentage = analysis_result.get("rank_change_percentage", 0)
                rank_change_msg = analysis_result.get("rank_change_msg", "")

                # 현재 보유 슬랏 수에 따라 매수 점수 기준 동적 조정
                current_slots = await self._get_current_slots_count()

                # 시장 상태에 따른 매수 점수 기준 조정
                min_score = 8  # 기본 기준

                # 약세장에서는 더 높은 기준, 강세장에서는 낮은 기준
                if self.market_condition == -1:  # 약세장
                    min_score = 9  # 더 엄격한 기준
                elif self.market_condition == 1:  # 강세장
                    min_score = 7  # 더 완화된 기준

                # 슬랏이 많이 차있을수록 더 높은 기준 적용
                if current_slots >= 7:  # 70% 이상 찼을 경우
                    min_score += 1

                # 진입 결정 확인
                buy_score = scenario.get("buy_score", 0)
                decision = analysis_result.get("decision")
                logger.info(f"매수 점수 체크: {company_name}({ticker}) - 점수: {buy_score}, 최소 요구 점수: {min_score}")

                # 거래대금 랭킹 상승 시 가중치 부여 (새로 추가)
                rank_bonus = 0
                if rank_change_percentage >= 30:
                    rank_bonus = 2  # 큰 폭 상승 시 2점 보너스
                    logger.info(f"거래대금 랭킹 큰 폭 상승으로 매수 점수 +2 보너스: {company_name}({ticker})")
                elif rank_change_percentage >= 15:
                    rank_bonus = 1  # 중간 수준 상승 시 1점 보너스
                    logger.info(f"거래대금 랭킹 상승으로 매수 점수 +1 보너스: {company_name}({ticker})")

                effective_buy_score = buy_score + rank_bonus
                logger.info(f"최종 매수 점수: {effective_buy_score} (기본: {buy_score}, 랭킹 보너스: {rank_bonus})")

                # 매수하지 않는 경우 (관망/점수 부족/산업군 제약) 메시지 생성
                if decision != "진입" or effective_buy_score < min_score or not sector_diverse:
                    # 매수하지 않는 이유 결정
                    reason = ""
                    if not sector_diverse:
                        reason = f"산업군 '{sector}' 과다 투자 방지"
                    elif effective_buy_score < min_score:
                        if decision == "진입":
                            decision = "관망"  # "진입"에서 "관망"으로 변경
                            logger.info(f"매수 점수 부족으로 결정 변경: {company_name}({ticker}) - 진입 → 관망 (점수: {effective_buy_score} < {min_score})")
                        reason = f"매수 점수 부족 ({effective_buy_score} < {min_score})"
                    elif decision != "진입":
                        reason = f"분석 결정이 '관망'"

                    # 시장 상태 정보
                    market_condition_text = "강세장" if self.market_condition == 1 else "약세장" if self.market_condition == -1 else "중립"

                    # 관망 메시지 생성
                    skip_message = f"⚠️ 매수 보류: {company_name}({ticker})\n" \
                                   f"현재가: {current_price:,.0f}원\n" \
                                   f"매수 점수: {buy_score}/10 (보너스: +{rank_bonus})\n" \
                                   f"결정: {decision}\n" \
                                   f"시장 상태: {market_condition_text}\n" \
                                   f"산업군: {scenario.get('sector', '알 수 없음')}\n" \
                                   f"보류 이유: {reason}\n" \
                                   f"분석 의견: {scenario.get('rationale', '정보 없음')}"

                    self.message_queue.append(skip_message)
                    logger.info(f"매수 보류: {company_name}({ticker}) - {reason}")
                    continue

                # 진입 결정이면 매수 처리
                if decision == "진입" and effective_buy_score >= min_score and sector_diverse:
                    # 매수 처리
                    buy_success = await self.buy_stock(ticker, company_name, current_price, scenario, rank_change_msg)

                    if buy_success:
                        buy_count += 1
                        logger.info(f"매수 완료: {company_name}({ticker}) @ {current_price:,.0f}원")
                    else:
                        logger.warning(f"매수 실패: {company_name}({ticker})")

            logger.info(f"보고서 처리 완료 - 매수: {buy_count}건, 매도: {sell_count}건")
            return buy_count, sell_count

        except Exception as e:
            logger.error(f"보고서 처리 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return 0, 0

    async def buy_stock(self, ticker: str, company_name: str, current_price: float, scenario: Dict[str, Any], rank_change_msg: str = "") -> bool:
        """
        주식 매수 처리 (부모 클래스 메서드 오버라이드)
        """
        try:
            # 시나리오에 목표가/손절가가 없거나 0이면 동적으로 계산
            if scenario.get('target_price', 0) <= 0:
                target_price = await self._dynamic_target_price(ticker, current_price)
                scenario['target_price'] = target_price
                logger.info(f"{ticker} 동적 목표가 계산: {target_price:,.0f}원")

            if scenario.get('stop_loss', 0) <= 0:
                stop_loss = await self._dynamic_stop_loss(ticker, current_price)
                scenario['stop_loss'] = stop_loss
                logger.info(f"{ticker} 동적 손절가 계산: {stop_loss:,.0f}원")

            # 부모 클래스의 buy_stock 메서드 호출
            return await super().buy_stock(ticker, company_name, current_price, scenario, rank_change_msg)

        except Exception as e:
            logger.error(f"{ticker} 매수 처리 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _analyze_trend(self, ticker, days=14):
        """종목의 단기 추세 분석"""
        try:
            # 데이터 가져오기
            today = datetime.now()
            start_date = (today - timedelta(days=days)).strftime("%Y%m%d")
            end_date = today.strftime("%Y%m%d")

            from pykrx.stock import stock_api
            df = stock_api.get_market_ohlcv_by_date(start_date, end_date, ticker)

            if df.empty:
                return 0  # 중립 (데이터 없음)

            # 추세 계산
            prices = df['종가'].values
            x = np.arange(len(prices))

            # 선형 회귀로 추세 계산
            slope, _, _, _, _ = stats.linregress(x, prices)

            # 가격 변화량 대비 추세 강도 계산
            price_range = np.max(prices) - np.min(prices)
            normalized_slope = slope * len(prices) / price_range if price_range > 0 else 0

            # 임계값 기반 추세 판단
            if normalized_slope > 0.15:  # 강한 상승 추세
                return 2
            elif normalized_slope > 0.05:  # 약한 상승 추세
                return 1
            elif normalized_slope < -0.15:  # 강한 하락 추세
                return -2
            elif normalized_slope < -0.05:  # 약한 하락 추세
                return -1
            else:  # 중립 추세
                return 0

        except Exception as e:
            logger.error(f"{ticker} 추세 분석 중 오류: {str(e)}")
            return 0  # 오류 발생 시 중립 추세로 가정

    async def _analyze_sell_decision(self, stock_data):
        """개선된 매도 의사결정 분석"""
        try:
            ticker = stock_data.get('ticker', '')
            company_name = stock_data.get('company_name', '')
            buy_price = stock_data.get('buy_price', 0)
            buy_date = stock_data.get('buy_date', '')
            current_price = stock_data.get('current_price', 0)
            target_price = stock_data.get('target_price', 0)
            stop_loss = stock_data.get('stop_loss', 0)

            # 수익률 계산
            profit_rate = ((current_price - buy_price) / buy_price) * 100

            # 매수일로부터 경과 일수
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            days_passed = (datetime.now() - buy_datetime).days

            # 시나리오 정보 추출
            scenario_str = stock_data.get('scenario', '{}')
            investment_period = "중기"  # 기본값

            try:
                if isinstance(scenario_str, str):
                    scenario_data = json.loads(scenario_str)
                    investment_period = scenario_data.get('investment_period', '중기')
            except:
                pass

            # 종목의 추세 분석
            trend = await self._analyze_trend(ticker)

            # 매도 의사결정 우선순위에 따라 조건 체크

            # 1. 손절매 조건 확인 (가장 높은 우선순위)
            if stop_loss > 0 and current_price <= stop_loss:
                # 강한 상승 추세에서는 손절 유예 (예외 케이스)
                if trend >= 2 and profit_rate > -7:  # 강한 상승 추세 & 손실이 7% 미만
                    return False, "손절 유예 (강한 상승 추세)"
                return True, f"손절매 조건 도달 (손절가: {stop_loss:,.0f}원)"

            # 2. 목표가 도달 확인
            if target_price > 0 and current_price >= target_price:
                # 강한 상승 추세면 계속 보유 (예외 케이스)
                if trend >= 2:
                    return False, "목표가 달성했으나 강한 상승 추세로 보유 유지"
                return True, f"목표가 달성 (목표가: {target_price:,.0f}원)"

            # 3. 시장 상태와 추세에 따른 매도 조건 (시장 환경 고려)
            if self.market_condition == -1 and trend < 0 and profit_rate > 3:
                return True, f"약세장 + 하락 추세에서 수익 확보 (수익률: {profit_rate:.2f}%)"

            # 4. 투자 기간별 조건 (투자 유형에 따른 분화)
            if investment_period == "단기":
                # 단기 투자 수익 목표 달성
                if days_passed >= 15 and profit_rate >= 5 and trend < 2:
                    return True, f"단기 투자 목표 달성 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

                # 단기 투자 손실 방어 (단, 강한 상승 추세면 유지)
                if days_passed >= 10 and profit_rate <= -3 and trend < 2:
                    return True, f"단기 투자 손실 방어 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # 5. 일반적인 수익 목표 달성 (특별한 기간이 아닌 일반 투자)
            if profit_rate >= 10 and trend < 2:
                return True, f"수익률 10% 이상 달성 (현재 수익률: {profit_rate:.2f}%)"

            # 6. 장기 보유 후 상태 점검 (시간 경과에 따른 판단)
            # 손절가보다 높지만 장기간 손실이 지속되는 경우
            if days_passed >= 30 and profit_rate < 0 and trend < 1:
                return True, f"30일 이상 보유 중이며 손실 상태 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            if days_passed >= 60 and profit_rate >= 3 and trend < 1:
                return True, f"60일 이상 보유 중이며 3% 이상 수익 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # 7. 투자 유형별 장기 점검 (투자 기간 특화)
            if investment_period == "장기" and days_passed >= 90 and profit_rate < 0 and trend < 1:
                return True, f"장기 투자 손실 정리 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # 8. 손절가는 아니지만 급격한 손실 발생 (비상 대응)
            # 일반 손실 매도 조건은 손절가 이하가 아닌 경우에만 적용
            # 손절가가 설정되지 않았거나(0) 손절가보다 현재가가 높으면서 큰 손실(-5% 이상)이 있는 경우
            if (stop_loss == 0 or current_price > stop_loss) and profit_rate <= -5 and trend < 1:
                return True, f"심각한 손실 발생 (현재 수익률: {profit_rate:.2f}%)"

            # 기본적으로 계속 보유
            trend_text = {
                2: "강한 상승 추세", 1: "약한 상승 추세", 0: "중립 추세",
                -1: "약한 하락 추세", -2: "강한 하락 추세"
            }.get(trend, "알 수 없는 추세")

            return False, f"계속 보유 (추세: {trend_text}, 수익률: {profit_rate:.2f}%)"

        except Exception as e:
            logger.error(f"매도 분석 중 오류: {str(e)}")
            return False, "분석 오류"