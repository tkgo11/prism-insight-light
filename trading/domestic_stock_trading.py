"""
국내주식 매매 모듈
- 1종목당 정액 매수
- 시장가 매수/매도
- 전량 청산 매도
"""

import asyncio
import datetime
import logging
import math
from pathlib import Path
from typing import Optional, Dict, List, Any

import yaml

# 현재 파일이 있는 디렉토리의 경로
TRADING_DIR = Path(__file__).parent

# kis_auth import (같은 디렉토리)
import sys
sys.path.insert(0, str(TRADING_DIR))
import kis_auth as ka

# 로깅 설정
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 설정파일 로딩
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
with open(CONFIG_FILE, encoding="UTF-8") as f:
    _cfg = yaml.load(f, Loader=yaml.FullLoader)


class DomesticStockTrading:
    """국내주식 매매 클래스"""

    # 기본 매수 금액 단위
    DEFAULT_BUY_AMOUNT = _cfg["default_unit_amount"]
    # 자동매매 동작 여부
    AUTO_TRADING = _cfg["auto_trading"]
    # 기본 매매 환경
    DEFAULT_MODE = _cfg["default_mode"]

    def __init__(self, mode: str = DEFAULT_MODE, buy_amount: int = None, auto_trading:bool = AUTO_TRADING):
        """
        초기화

        Args:
            mode: 'demo' (모의투자) 또는 'real' (실전투자)
            buy_amount: 1종목당 매수 금액 단위 (기본값: yaml 파일 참고)
            auto_trading: 자동 트레이딩 실행 여부
        """
        self.mode = mode
        self.env = "vps" if mode == "demo" else "prod"
        self.buy_amount = buy_amount if buy_amount else self.DEFAULT_BUY_AMOUNT
        self.auto_trading = auto_trading

        # 인증
        ka.auth(svr=self.env, product="01")
        self.trenv = ka.getTREnv()

        # 비동기 처리를 위한 추가 설정
        self._global_lock = asyncio.Lock()  # 전역 계좌 접근 제어
        self._semaphore = asyncio.Semaphore(3)  # 최대 3개 동시 요청
        self._stock_locks = {}  # 종목별 락

        logger.info(f"DomesticStockTrading initialized (Async Enabled)")
        logger.info(f"Mode: {mode}, Buy Amount: {self.buy_amount:,}원")
        logger.info(f"Account: {self.trenv.my_acct}-{self.trenv.my_prod}")

    def get_current_price(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        현재 시장가 조회 (연동 테스트 겸용)

        Args:
            stock_code: 종목코드 (6자리)

        Returns:
            {
                'stock_code': '종목코드',
                'stock_name': '종목명',
                'current_price': 현재가,
                'change_rate': 전일대비율,
                'volume': 거래량
            }
        """
        api_url = "/uapi/domestic-stock/v1/quotations/inquire-price"
        tr_id = "FHKST01010100"

        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": stock_code
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params)

            if res.isOK():
                data = res.getBody().output

                result = {
                    'stock_code': stock_code,
                    'stock_name': data.get('rprs_mrkt_kor_name', ''),
                    'current_price': int(data.get('stck_prpr', 0)),  # 현재가
                    'change_rate': float(data.get('prdy_ctrt', 0)),  # 전일대비율
                    'volume': int(data.get('acml_vol', 0))  # 누적거래량
                }

                logger.info(f"[{stock_code}] 현재가: {result['current_price']:,}원 ({result['change_rate']:+.2f}%)")
                return result
            else:
                logger.error(f"현재가 조회 실패: {res.getErrorCode()} - {res.getErrorMessage()}")
                return None

        except Exception as e:
            logger.error(f"현재가 조회 중 오류: {str(e)}")
            return None

    def calculate_buy_quantity(self, stock_code: str, buy_amount: int = None) -> int:
        """
        매수 가능 수량 계산

        Args:
            stock_code: 종목코드
            buy_amount: 매수 금액 (기본값: 초기화시 설정한 금액)

        Returns:
            매수 가능 수량 (0이면 매수 불가)
        """
        amount = buy_amount if buy_amount else self.buy_amount

        # 현재가 조회
        current_price_info = self.get_current_price(stock_code)
        if not current_price_info:
            return 0

        current_price = current_price_info['current_price']

        # 매수 가능 수량 계산 (소수점 버림)
        current_quantity = math.floor(amount / current_price)

        if current_quantity == 0:
            logger.warning(f"[{stock_code}] 현재가 {current_price:,}원 > 매수금액 {amount:,}원 - 매수 불가")
        else:
            total_amount = current_quantity * current_price
            logger.info(f"[{stock_code}] 매수 가능: {current_quantity}주 x {current_price:,}원 = {total_amount:,}원")

        return current_quantity

    def buy_market_price(self, stock_code: str, buy_amount: int = None) -> Dict[str, Any]:
        """
        시장가 매수

        Args:
            stock_code: 종목코드
            buy_amount: 매수 금액 (기본값: 초기화시 설정한 금액)

        Returns:
            {
                'success': 성공 여부,
                'order_no': 주문번호,
                'stock_code': 종목코드,
                'quantity': 주문수량,
                'message': 메시지
            }
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '자동매매가 비활성화되어 있습니다. 매수 작업을 수행할 수 없습니다. (AUTO_TRADING=False)'
            }


        # 매수 가능 수량 계산
        buy_quantity = self.calculate_buy_quantity(stock_code, buy_amount)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '매수 가능 수량이 0입니다 (현재가가 매수금액보다 높음)'
            }

        # 매수 주문 실행
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"

        # TR ID 설정 (실전/모의 구분)
        if self.mode == "real":
            tr_id = "TTTC0012U"  # 실전 매수
        else:
            tr_id = "VTTC0012U"  # 모의 매수

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_DVSN": "01",  # 01: 시장가
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": "0",  # 시장가는 0
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "",
            "CNDT_PRIC": ""
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('odno', '')

                logger.info(f"[{stock_code}] 시장가 매수 주문 성공: {buy_quantity}주, 주문번호: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'시장가 매수 주문 완료 ({buy_quantity}주)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"매수 주문 실패: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'매수 주문 실패: {error_msg}'
                }

        except Exception as e:
            logger.error(f"매수 주문 중 오류: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'message': f'매수 주문 중 오류: {str(e)}'
            }

    def get_holding_quantity(self, stock_code: str) -> int:
        """
        특정 종목의 보유 수량 조회

        Args:
            stock_code: 종목코드

        Returns:
            보유 수량 (없으면 0)
        """
        current_portfolio = self.get_portfolio()

        for current_stock in current_portfolio:
            if current_stock['stock_code'] == stock_code:
                return current_stock['quantity']

        return 0

    def buy_limit_price(self, stock_code: str, limit_price: int, buy_amount: int = None) -> Dict[str, Any]:
        """
        지정가 매수

        Args:
            stock_code: 종목코드
            limit_price: 지정가격
            buy_amount: 매수 금액 (기본값: 초기화시 설정한 금액)

        Returns:
            {
                'success': 성공 여부,
                'order_no': 주문번호,
                'stock_code': 종목코드,
                'quantity': 주문수량,
                'limit_price': 지정가격,
                'message': 메시지
            }
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'limit_price': limit_price,
                'message': '자동매매가 비활성화되어 있습니다. 매수 작업을 수행할 수 없습니다. (AUTO_TRADING=False)'
            }

        amount = buy_amount if buy_amount else self.buy_amount

        # 매수 가능 수량 계산 (지정가 기준)
        buy_quantity = math.floor(amount / limit_price)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'limit_price': limit_price,
                'message': f'매수 가능 수량이 0입니다 (지정가 {limit_price:,}원 > 매수금액 {amount:,}원)'
            }

        # 지정가 매수 주문 실행
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"

        if self.mode == "real":
            tr_id = "TTTC0012U"  # 실전 매수
        else:
            tr_id = "VTTC0012U"  # 모의 매수

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_DVSN": "00",  # 00: 지정가
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": str(limit_price),  # 지정가격
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "",
            "CNDT_PRIC": ""
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('odno', '')

                logger.info(f"[{stock_code}] 지정가 매수 주문 성공: {buy_quantity}주 x {limit_price:,}원, 주문번호: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'limit_price': limit_price,
                    'message': f'지정가 매수 주문 완료 ({buy_quantity}주 x {limit_price:,}원)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"지정가 매수 주문 실패: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'limit_price': limit_price,
                    'message': f'매수 주문 실패: {error_msg}'
                }

        except Exception as e:
            logger.error(f"지정가 매수 주문 중 오류: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'limit_price': limit_price,
                'message': f'매수 주문 중 오류: {str(e)}'
            }

    def smart_buy(self, stock_code: str, buy_amount: int = None) -> Dict[str, Any]:
        """
        시간대에 따라 자동으로 최적의 방법으로 매수

        - 09:00~15:30: 시장가 매수
        - 15:40~16:00: 시간외 종가매매
        - 16:00~18:00: 시간외 단일가매매
        - 그외 시간: 예약주문 (다음날 시장가)

        Args:
            stock_code: 종목코드
            buy_amount: 매수 금액 (기본값: 초기화시 설정한 금액)

        Returns:
            매수 결과
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '자동매매가 비활성화되어 있습니다. 매수 작업을 수행할 수 없습니다. (AUTO_TRADING=False)'
            }

        now = datetime.datetime.now()
        current_time = now.time()

        # 시간대별 분기
        if datetime.time(9, 0) <= current_time <= datetime.time(15, 30):
            # 정규장
            logger.info(f"[{stock_code}] 정규장 시간 - 시장가 매수 실행")
            return self.buy_market_price(stock_code, buy_amount)

        elif datetime.time(15, 40) <= current_time <= datetime.time(16, 0):
            # 시간외 종가매매
            logger.info(f"[{stock_code}] 시간외 종가매매 시간 - 종가매수 실행")
            return self.buy_closing_price(stock_code, buy_amount)

        elif datetime.time(16, 0) <= current_time <= datetime.time(18, 0):
            # 시간외 단일가매매
            logger.info(f"[{stock_code}] 시간외 단일가매매 시간")
            return self.buy_after_market(stock_code, buy_amount)

        else:
            # 예약주문
            logger.info(f"[{stock_code}] 장외 시간 - 예약주문 실행")
            return self.buy_reserved_order(stock_code, buy_amount)

    def buy_closing_price(self, stock_code: str, buy_amount: int = None) -> Dict[str, Any]:
        """
        시간외 종가매매로 매수 (15:40~16:00)
        당일 종가로 매수

        Args:
            stock_code: 종목코드
            buy_amount: 매수 금액 (기본값: 초기화시 설정한 금액)

        Returns:
            매수 결과
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '자동매매가 비활성화되어 있습니다. 매수 작업을 수행할 수 없습니다. (AUTO_TRADING=False)'
            }

        # 매수 가능 수량 계산
        buy_quantity = self.calculate_buy_quantity(stock_code, buy_amount)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '매수 가능 수량이 0입니다'
            }

        # 시간외 종가매매 매수
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"

        if self.mode == "real":
            tr_id = "TTTC0012U"
        else:
            tr_id = "VTTC0012U"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_DVSN": "02",  # 02: 시간외 종가
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": "0",  # 종가매매는 0
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "",
            "CNDT_PRIC": ""
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('odno', '')

                logger.info(f"[{stock_code}] 시간외 종가 매수 주문 성공: {buy_quantity}주, 주문번호: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'시간외 종가 매수 주문 완료 ({buy_quantity}주)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"시간외 종가 매수 실패: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'매수 주문 실패: {error_msg}'
                }

        except Exception as e:
            logger.error(f"시간외 종가 매수 중 오류: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': quantity,
                'message': f'매수 주문 중 오류: {str(e)}'
            }

    def buy_after_market(self, stock_code: str, buy_amount: int = None) -> Dict[str, Any]:
        """
        시간외 단일가매매로 매수 (16:00~18:00)

        Args:
            stock_code: 종목코드
            buy_amount: 매수 금액 (기본값: 초기화시 설정한 금액)

        Returns:
            매수 결과
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '자동매매가 비활성화되어 있습니다. 매수 작업을 수행할 수 없습니다. (AUTO_TRADING=False)'
            }

        # 매수 가능 수량 계산
        buy_quantity = self.calculate_buy_quantity(stock_code, buy_amount)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '매수 가능 수량이 0입니다'
            }

        # 시간외 단일가매매 매수
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"

        if self.mode == "real":
            tr_id = "TTTC0012U"
        else:
            tr_id = "VTTC0012U"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_DVSN": "06",  # 06: 시간외 단일가
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": "0",  # 시간외 단일가는 0
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "",
            "CNDT_PRIC": ""
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('odno', '')

                logger.info(f"[{stock_code}] 시간외 단일가 매수 주문 성공: {buy_quantity}주, 주문번호: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'시간외 단일가 매수 주문 완료 ({buy_quantity}주)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"시간외 단일가 매수 실패: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'매수 주문 실패: {error_msg}'
                }

        except Exception as e:
            logger.error(f"시간외 단일가 매수 중 오류: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'message': f'매수 주문 중 오류: {str(e)}'
            }

    def buy_reserved_order(self, stock_code: str, buy_amount: int = None, end_date: str = None) -> Dict[str, Any]:
        """
        예약주문으로 매수 (다음 거래일 자동 실행)
        예약주문 가능시간: 15:40~다음 영업일 07:30 (23:40~00:10 제외)

        Args:
            stock_code: 종목코드
            buy_amount: 매수 금액 (기본값: 초기화시 설정한 금액)
            end_date: 기간예약 종료일 (YYYYMMDD 형식, None이면 일반예약주문)

        Returns:
            매수 결과
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '자동매매가 비활성화되어 있습니다. 매수 작업을 수행할 수 없습니다. (AUTO_TRADING=False)'
            }

        amount = buy_amount if buy_amount else self.buy_amount

        # 주문 구분 및 단가 설정
        ord_dvsn_cd = "01"  # 시장가
        ord_unpr = "0"
        # 시장가의 경우 현재가 기준으로 수량 계산
        buy_quantity = self.calculate_buy_quantity(stock_code, amount)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '매수 가능 수량이 0입니다'
            }

        # 예약주문 API 호출
        api_url = "/uapi/domestic-stock/v1/trading/order-resv"
        tr_id = "CTSC0008U"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": ord_unpr,
            "SLL_BUY_DVSN_CD": "02",  # 02: 매수
            "ORD_DVSN_CD": ord_dvsn_cd,
            "ORD_OBJT_CBLC_DVSN_CD": "10",  # 10: 현금
            "LOAN_DT": "",
            "LDNG_DT": ""
        }

        # 기간예약주문인 경우 종료일 추가
        if end_date:
            params["RSVN_ORD_END_DT"] = end_date
        else:
            params["RSVN_ORD_END_DT"] = ""

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('RSVN_ORD_RCIT_UNPR', '')  # 예약주문접수단가

                order_type_str = {
                    "01": "시장가",
                    "00": f"지정가({ord_unpr}원)",
                    "05": "장전 시간외"
                }.get(ord_dvsn_cd, "")

                period_str = f"기간예약(~{end_date})" if end_date else "일반예약"

                logger.info(f"[{stock_code}] 예약주문 매수 성공: {buy_quantity}주, {order_type_str}, {period_str}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'order_type': order_type_str,
                    'period_type': period_str,
                    'message': f'예약주문 매수 완료 ({buy_quantity}주, {order_type_str}, {period_str})'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"예약주문 매수 실패: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'예약주문 실패: {error_msg}'
                }

        except Exception as e:
            logger.error(f"예약주문 매수 중 오류: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'message': f'예약주문 중 오류: {str(e)}'
            }

    def sell_all_market_price(self, stock_code: str) -> Dict[str, Any]:
        """
        시장가 전량 매도 (보유 수량 전체 청산)

        Args:
            stock_code: 종목코드

        Returns:
            {
                'success': 성공 여부,
                'order_no': 주문번호,
                'stock_code': 종목코드,
                'quantity': 매도수량,
                'message': 메시지
            }
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '자동매매가 비활성화되어 있습니다. 매도 작업을 수행할 수 없습니다. (AUTO_TRADING=False)'
            }

        # 보유 수량 확인
        buy_quantity = self.get_holding_quantity(stock_code)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '보유 수량이 없습니다'
            }

        # 매도 주문 실행
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"

        # TR ID 설정 (실전/모의 구분)
        if self.mode == "real":
            tr_id = "TTTC0011U"  # 실전 매도
        else:
            tr_id = "VTTC0011U"  # 모의 매도

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_DVSN": "01",  # 01: 시장가
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": "0",  # 시장가는 0
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "01",  # 01: 일반매도
            "CNDT_PRIC": ""
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('odno', '')

                logger.info(f"[{stock_code}] 시장가 전량 매도 주문 성공: {buy_quantity}주, 주문번호: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'시장가 전량 매도 주문 완료 ({buy_quantity}주)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"매도 주문 실패: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'매도 주문 실패: {error_msg}'
                }

        except Exception as e:
            logger.error(f"매도 주문 중 오류: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': quantity,
                'message': f'매도 주문 중 오류: {str(e)}'
            }

    def smart_sell_all(self, stock_code: str) -> Dict[str, Any]:
        """
        시간대에 따라 자동으로 최적의 방법으로 전량매도

        - 09:00~15:30: 시장가 매도
        - 15:40~16:00: 시간외 종가매매
        - 16:00~18:00: 시간외 단일가매매 (현재가 기준)
        - 그외 시간: 예약주문 (다음날 시장가)

        Args:
            stock_code: 종목코드

        Returns:
            매도 결과
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '자동매매가 비활성화되어 있습니다. 매도 작업을 수행할 수 없습니다. (AUTO_TRADING=False)'
            }

        now = datetime.datetime.now()
        current_time = now.time()

        # 시간대별 분기
        if datetime.time(9, 0) <= current_time <= datetime.time(15, 30):
            # 정규장 - 시장가 매도
            logger.info(f"[{stock_code}] 정규장 시간 - 시장가 매도 실행")
            return self.sell_all_market_price(stock_code)

        elif datetime.time(15, 40) <= current_time <= datetime.time(16, 0):
            # 시간외 종가매매
            logger.info(f"[{stock_code}] 시간외 종가매매 시간 - 종가매도 실행")
            return self.sell_all_closing_price(stock_code)

        elif datetime.time(16, 0) <= current_time <= datetime.time(18, 0):
            # 시간외 단일가매매 - 현재가 기준 지정가
            logger.info(f"[{stock_code}] 시간외 단일가매매 시간 - 현재가 기준 매도")
            return self.sell_all_after_market_limit(stock_code)

        else:
            # 예약주문 (다음날 시장가) - 수정된 함수 호출
            logger.info(f"[{stock_code}] 장외 시간 - 예약주문 실행")
            return self.sell_all_reserved_order(stock_code)

    def sell_all_after_market_limit(self, stock_code: str) -> Dict[str, Any]:
        """
        시간외 단일가매매로 전량매도 (16:00~18:00)
        현재가 기준으로 약간 낮은 가격에 지정가 매도
        """
        # 보유 수량 확인
        holding_quantity = self.get_holding_quantity(stock_code)
        if holding_quantity == 0:
            return {'success': False, 'message': '보유 수량이 없습니다'}

        # 시간외 단일가매매 매도
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"

        if self.mode == "real":
            tr_id = "TTTC0011U"
        else:
            tr_id = "VTTC0011U"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_DVSN": "07",  # 07: 시간외 단일가
            "ORD_QTY": str(holding_quantity),
            "ORD_UNPR": str(0),
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "01",
            "CNDT_PRIC": ""
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('odno', '')

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': holding_quantity,
                    'message': f'시간외 단일가 매도 완료 ({holding_quantity}주)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return {'success': False, 'message': f'매도 실패: {error_msg}'}

        except Exception as e:
            return {'success': False, 'message': f'매도 중 오류: {str(e)}'}

    def sell_all_closing_price(self, stock_code: str) -> Dict[str, Any]:
        """
        시간외 종가매매로 전량매도 (15:40~16:00)
        당일 종가로 매도
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '자동매매가 비활성화되어 있습니다. 매도 작업을 수행할 수 없습니다. (AUTO_TRADING=False)'
            }

        # 보유 수량 확인
        buy_quantity = self.get_holding_quantity(stock_code)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '보유 수량이 없습니다'
            }

        # 시간외 종가매매 매도
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"

        if self.mode == "real":
            tr_id = "TTTC0011U"
        else:
            tr_id = "VTTC0011U"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_DVSN": "06",  # 06: 장후 시간외
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": "0",  # 종가매매는 0
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "01",
            "CNDT_PRIC": ""
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('odno', '')

                logger.info(f"[{stock_code}] 시간외 종가 매도 주문 성공: {buy_quantity}주, 주문번호: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'시간외 종가 매도 완료 ({buy_quantity}주)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'매도 실패: {error_msg}'
                }

        except Exception as e:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'message': f'매도 중 오류: {str(e)}'
            }

    def sell_all_reserved_order(self, stock_code: str, end_date: str = None) -> Dict[str, Any]:
        """
        예약주문으로 전량매도 (다음 거래일 자동 실행)
        예약주문 가능시간: 15:40~다음 영업일 07:30 (23:40~00:10 제외)

        Args:
            stock_code: 종목코드
            end_date: 기간예약 종료일 (YYYYMMDD 형식, None이면 일반예약주문)

        Returns:
            매도 결과
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '자동매매가 비활성화되어 있습니다. 매도 작업을 수행할 수 없습니다. (AUTO_TRADING=False)'
            }

        # 보유 수량 확인
        buy_quantity = self.get_holding_quantity(stock_code)
        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': '보유 수량이 없습니다'
            }

        # 주문 구분 및 단가 설정
        ord_dvsn_cd = "01"  # 시장가
        ord_unpr = "0"

        # 예약주문 API 호출
        api_url = "/uapi/domestic-stock/v1/trading/order-resv"
        tr_id = "CTSC0008U"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": ord_unpr,
            "SLL_BUY_DVSN_CD": "01",  # 01: 매도
            "ORD_DVSN_CD": ord_dvsn_cd,
            "ORD_OBJT_CBLC_DVSN_CD": "10",  # 10: 현금
            "LOAN_DT": "",
            "LDNG_DT": ""
        }

        # 기간예약주문인 경우 종료일 추가
        if end_date:
            params["RSVN_ORD_END_DT"] = end_date
        else:
            params["RSVN_ORD_END_DT"] = ""

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('RSVN_ORD_RCIT_UNPR', '')  # 예약주문접수단가

                order_type_str = {
                    "01": "시장가",
                    "00": f"지정가({ord_unpr}원)",
                    "05": "장전 시간외"
                }.get(ord_dvsn_cd, "")

                period_str = f"기간예약(~{end_date})" if end_date else "일반예약"

                logger.info(f"[{stock_code}] 예약주문 매도 성공: {buy_quantity}주, {order_type_str}, {period_str}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'order_type': order_type_str,
                    'period_type': period_str,
                    'message': f'예약주문 매도 완료 ({buy_quantity}주, {order_type_str}, {period_str})'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"예약주문 매도 실패: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'예약주문 실패: {error_msg}'
                }

        except Exception as e:
            logger.error(f"예약주문 매도 중 오류: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'message': f'예약주문 중 오류: {str(e)}'
            }

    async def _get_stock_lock(self, stock_code: str) -> asyncio.Lock:
        """종목별 락 반환 (동시 매매 방지)"""
        if stock_code not in self._stock_locks:
            self._stock_locks[stock_code] = asyncio.Lock()
        return self._stock_locks[stock_code]

    async def async_buy_stock(self, stock_code: str, buy_amount: int = None, timeout: float = 30.0) -> Dict[str, Any]:
        """
        비동기 매수 API (타임아웃 포함)
        현재가 조회 → 매수 가능 수량 계산 → 시장가 매수

        Args:
            stock_code: 종목코드 (6자리)
            buy_amount: 매수 금액 (기본값: 초기화시 설정한 금액)
            timeout: 타임아웃 시간(초)

        Returns:
            {
                'success': 성공 여부,
                'stock_code': 종목코드,
                'current_price': 매수시점 현재가,
                'quantity': 매수 수량,
                'total_amount': 총 매수 금액,
                'order_no': 주문번호,
                'message': 결과 메시지,
                'timestamp': 실행 시간
            }
        """
        try:
            return await asyncio.wait_for(
                self._execute_buy_stock(stock_code, buy_amount),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return {
                'success': False,
                'stock_code': stock_code,
                'current_price': 0,
                'quantity': 0,
                'total_amount': 0,
                'order_no': None,
                'message': f'매수 요청 타임아웃 ({timeout}초)',
                'timestamp': datetime.datetime.now().isoformat()
            }

    async def _execute_buy_stock(self, stock_code: str, buy_amount: int = None) -> Dict[str, Any]:
        # buy_amount가 None이면 클래스 기본값 사용
        amount = buy_amount if buy_amount else self.buy_amount

        result = {
            'success': False,
            'stock_code': stock_code,
            'current_price': 0,
            'quantity': 0,
            'total_amount': 0,
            'order_no': None,
            'message': '',
            'timestamp': datetime.datetime.now().isoformat()
        }

        # 종목별 락 + 세마포어 + 전역 락으로 3단계 보호
        stock_lock = await self._get_stock_lock(stock_code)

        async with stock_lock:  # 1단계: 종목별 동시 매매 방지
            async with self._semaphore:  # 2단계: 전체 동시 요청 수 제한
                async with self._global_lock:  # 3단계: 계좌 정보 보호
                    try:
                        logger.info(f"[비동기 매수 API] {stock_code} 매수 프로세스 시작 (금액: {amount:,}원)")

                        # 1단계: 현재가 조회
                        current_price_info = await asyncio.to_thread(
                            self.get_current_price, stock_code
                        )

                        if not current_price_info:
                            result['message'] = '현재가 조회 실패'
                            logger.error(f"[비동기 매수 API] {stock_code} 현재가 조회 실패")
                            return result

                        result['current_price'] = current_price_info['current_price']

                        # 2단계: 매수 가능 수량 계산 (amount 사용)
                        buy_quantity = await asyncio.to_thread(
                            self.calculate_buy_quantity, stock_code, amount  # amount 사용
                        )

                        if buy_quantity == 0:
                            result['message'] = f'매수 가능 수량이 0입니다 (매수금액: {amount:,}원)'
                            logger.warning(f"[비동기 매수 API] {stock_code} 매수 가능 수량 0")
                            return result

                        result['quantity'] = buy_quantity
                        result['total_amount'] = buy_quantity * current_price_info['current_price']

                        # 3단계: 시장가 매수 실행 (amount 사용)
                        logger.info(f"[비동기 매수 API] {stock_code} 시장가 매수 실행: {buy_quantity}주 x {amount:,}원")
                        buy_result = await asyncio.to_thread(
                            self.smart_buy, stock_code, amount  # amount 사용
                        )

                        if buy_result['success']:
                            result['success'] = True
                            result['order_no'] = buy_result['order_no']
                            result['message'] = f"매수 완료: {buy_quantity}주 x {current_price_info['current_price']:,}원 = {result['total_amount']:,}원"
                            logger.info(f"[비동기 매수 API] {stock_code} 매수 성공")
                        else:
                            result['message'] = f"매수 실패: {buy_result['message']}"
                            logger.error(f"[비동기 매수 API] {stock_code} 매수 실패: {buy_result['message']}")

                    except Exception as e:
                        result['message'] = f'비동기 매수 API 실행 중 오류: {str(e)}'
                        logger.error(f"[비동기 매수 API] {stock_code} 오류: {str(e)}")

                    # API 부하 방지를 위한 딜레이
                    await asyncio.sleep(0.1)

        return result

    async def async_sell_stock(self, stock_code: str, timeout: float = 30.0) -> Dict[str, Any]:
        """
        비동기 매도 API (타임아웃 포함)
        보유 수량 전량 시장가 매도

        Args:
            stock_code: 종목코드 (6자리)
            timeout: 타임아웃 시간(초)

        Returns:
            {
                'success': 성공 여부,
                'stock_code': 종목코드,
                'current_price': 매도시점 현재가,
                'quantity': 매도 수량,
                'estimated_amount': 예상 매도 금액,
                'order_no': 주문번호,
                'message': 결과 메시지,
                'timestamp': 실행 시간
            }
        """
        try:
            return await asyncio.wait_for(
                self._execute_sell_stock(stock_code),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return {
                'success': False,
                'stock_code': stock_code,
                'current_price': 0,
                'quantity': 0,
                'estimated_amount': 0,
                'order_no': None,
                'message': f'매도 요청 타임아웃 ({timeout}초)',
                'timestamp': datetime.datetime.now().isoformat()
            }

    async def _execute_sell_stock(self, stock_code: str) -> Dict[str, Any]:
        """실제 매도 실행 로직 (포트폴리오 확인 방어로직 포함)"""
        result = {
            'success': False,
            'stock_code': stock_code,
            'current_price': 0,
            'quantity': 0,
            'estimated_amount': 0,
            'order_no': None,
            'message': '',
            'timestamp': datetime.datetime.now().isoformat()
        }

        # 종목별 락 + 세마포어 + 전역 락으로 3단계 보호
        stock_lock = await self._get_stock_lock(stock_code)

        async with stock_lock:  # 1단계: 종목별 동시 매매 방지
            async with self._semaphore:  # 2단계: 전체 동시 요청 수 제한
                async with self._global_lock:  # 3단계: 계좌 정보 보호
                    try:
                        logger.info(f"[비동기 매도 API] {stock_code} 매도 프로세스 시작")

                        # 방어로직 1: 포트폴리오에서 보유 종목 확인
                        logger.info(f"[비동기 매도 API] {stock_code} 포트폴리오 확인 중...")
                        current_portfolio = await asyncio.to_thread(self.get_portfolio)

                        # 해당 종목이 포트폴리오에 있는지 확인
                        target_stock = None
                        for current_stock in current_portfolio:
                            if stock['stock_code'] == stock_code:
                                target_stock = current_stock
                                break

                        if not target_stock:
                            result['message'] = f'포트폴리오에 {stock_code} 종목이 없습니다'
                            logger.warning(f"[비동기 매도 API] {stock_code} 포트폴리오에 없음")
                            return result

                        if target_stock['quantity'] <= 0:
                            result['message'] = f'{stock_code} 보유 수량이 0입니다'
                            logger.warning(f"[비동기 매도 API] {stock_code} 보유수량 0")
                            return result

                        logger.info(f"[비동기 매도 API] {stock_code} 보유 확인: {target_stock['quantity']}주")

                        # 현재가 조회 (예상 매도 금액 계산용)
                        current_price_info = await asyncio.to_thread(
                            self.get_current_price, stock_code
                        )

                        if current_price_info:
                            result['current_price'] = current_price_info['current_price']
                            logger.info(f"[비동기 매도 API] {stock_code} 현재가: {current_price_info['current_price']:,}원")

                        # 방어로직 2: 매도 전 한번 더 보유 수량 확인
                        holding_quantity = await asyncio.to_thread(
                            self.get_holding_quantity, stock_code
                        )

                        if holding_quantity <= 0:
                            result['message'] = f'{stock_code} 최종 확인 시 보유 수량이 0입니다'
                            logger.warning(f"[비동기 매도 API] {stock_code} 최종 확인 시 보유수량 0")
                            return result

                        # 전량 매도 실행
                        logger.info(f"[비동기 매도 API] {stock_code} 전량 매도 실행 (보유: {holding_quantity}주)")
                        all_sell_result = await asyncio.to_thread(
                            self.smart_sell_all, stock_code
                        )

                        if all_sell_result['success']:
                            result['success'] = True
                            result['quantity'] = all_sell_result['quantity']
                            result['order_no'] = all_sell_result['order_no']

                            # 예상 매도 금액 계산
                            if result['current_price'] > 0:
                                result['estimated_amount'] = result['quantity'] * result['current_price']

                            # 포트폴리오 정보 추가
                            result['avg_price'] = target_stock['avg_price']
                            result['profit_amount'] = target_stock['profit_amount']
                            result['profit_rate'] = target_stock['profit_rate']

                            result['message'] = (f"매도 완료: {result['quantity']}주 "
                                                 f"(평균단가: {result['avg_price']:,.0f}원, "
                                                 f"예상금액: {result['estimated_amount']:,}원, "
                                                 f"수익률: {result['profit_rate']:+.2f}%)")

                            logger.info(f"[비동기 매도 API] {stock_code} 매도 성공")
                        else:
                            result['message'] = f"매도 실패: {all_sell_result['message']}"
                            logger.error(f"[비동기 매도 API] {stock_code} 매도 실패: {all_sell_result['message']}")

                    except Exception as e:
                        result['message'] = f'비동기 매도 API 실행 중 오류: {str(e)}'
                        logger.error(f"[비동기 매도 API] {stock_code} 오류: {str(e)}")

                    # API 부하 방지를 위한 딜레이
                    await asyncio.sleep(0.1)

        return result

    def get_portfolio(self) -> List[Dict[str, Any]]:
        """
        현재 계좌의 포트폴리오 조회

        Returns:
            [{
                'stock_code': '종목코드',
                'stock_name': '종목명',
                'quantity': 보유수량,
                'avg_price': 평균단가,
                'current_price': 현재가,
                'eval_amount': 평가금액,
                'profit_amount': 평가손익,
                'profit_rate': 수익률(%)
            }, ...]
        """
        api_url = "/uapi/domestic-stock/v1/trading/inquire-balance"

        # TR ID 설정 (실전/모의 구분)
        if self.mode == "real":
            tr_id = "TTTC8434R"
        else:
            tr_id = "VTTC8434R"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params)

            if res.isOK():
                current_portfolio = []
                output1 = res.getBody().output1  # 보유종목 리스트
                output2 = res.getBody().output2[0]  # 계좌 요약 정보

                # output1이 리스트가 아닌 경우 처리
                if not isinstance(output1, list):
                    output1 = [output1] if output1 else []

                for item in output1:
                    # 보유수량이 0보다 큰 종목만 추가
                    quantity = int(item.get('hldg_qty', 0))
                    if quantity > 0:
                        stock_info = {
                            'stock_code': item.get('pdno', ''),
                            'stock_name': item.get('prdt_name', ''),
                            'quantity': quantity,
                            'avg_price': float(item.get('pchs_avg_pric', 0)),
                            'current_price': float(item.get('prpr', 0)),
                            'eval_amount': float(item.get('evlu_amt', 0)),
                            'profit_amount': float(item.get('evlu_pfls_amt', 0)),
                            'profit_rate': float(item.get('evlu_pfls_rt', 0))
                        }
                        current_portfolio.append(stock_info)

                # 계좌 요약 정보 로깅
                if output2:
                    total_eval = float(output2.get('tot_evlu_amt', 0))
                    total_profit = float(output2.get('evlu_pfls_smtl_amt', 0))
                    logger.info(f"계좌 총평가: {total_eval:,.0f}원, 총손익: {total_profit:+,.0f}원")

                logger.info(f"포트폴리오: {len(current_portfolio)}개 종목 보유")
                return current_portfolio

            else:
                logger.error(f"잔고 조회 실패: {res.getErrorCode()} - {res.getErrorMessage()}")
                return []

        except Exception as e:
            logger.error(f"잔고 조회 중 오류: {str(e)}")
            return []

    def get_account_summary(self) -> None | dict[Any, Any] | dict[str, float]:
        """
        계좌 요약 정보 조회

        Returns:
            {
                'total_eval_amount': 총평가금액,
                'total_profit_amount': 총평가손익,
                'total_profit_rate': 총수익률,
                'deposit': 예수금,
                'available_amount': 주문가능금액
            }
        """
        api_url = "/uapi/domestic-stock/v1/trading/inquire-balance"

        # TR ID 설정 (실전/모의 구분)
        if self.mode == "real":
            tr_id = "TTTC8434R"
        else:
            tr_id = "VTTC8434R"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params)

            if res.isOK():
                output2 = res.getBody().output2[0]  # 계좌 요약 정보

                if output2:
                    account_summary = {
                        'total_eval_amount': float(output2.get('tot_evlu_amt', 0)),
                        'total_profit_amount': float(output2.get('evlu_pfls_smtl_amt', 0)),
                        'total_profit_rate': float(output2.get('evlu_pfls_rt', 0)),
                        'deposit': float(output2.get('dnca_tot_amt', 0)),
                        'available_amount': float(output2.get('ord_psbl_cash', 0))
                    }

                    logger.info(f"계좌 요약: 총평가 {account_summary['total_eval_amount']:,.0f}원, "
                                f"손익 {account_summary['total_profit_amount']:+,.0f}원 "
                                f"({account_summary['total_profit_rate']:+.2f}%)")

                    return account_summary

                return {}

        except Exception as e:
            logger.error(f"계좌 요약 조회 중 오류: {str(e)}")
            return {}


# 컨텍스트 매니저
class AsyncTradingContext:
    """비동기 트레이딩 컨텍스트 매니저 (안전한 리소스 관리)"""
    # 기본 매수 금액 단위
    DEFAULT_BUY_AMOUNT = _cfg["default_unit_amount"]
    # 자동매매 동작 여부
    AUTO_TRADING = _cfg["auto_trading"]
    # 기본 매매 환경
    DEFAULT_MODE = _cfg["default_mode"]

    def __init__(self, mode: str = DEFAULT_MODE, buy_amount: int = None, auto_trading: bool = AUTO_TRADING):
        self.mode = mode
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading
        self.trader = None

    async def __aenter__(self):
        self.trader = DomesticStockTrading(mode=self.mode, buy_amount=self.buy_amount, auto_trading=self.auto_trading)
        return self.trader

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            logger.error(f"AsyncTradingContext 오류: {exc_type.__name__}: {exc_val}")

# ========== 테스트 코드 ==========
if __name__ == "__main__":
    """
    사용 예제 및 테스트
    """

    # 1. 초기화
    trader = DomesticStockTrading()

    # 2. 연동 테스트 - 현재가 조회
    print("\n=== 1. 현재가 조회 (연동 테스트) ===")
    price_info = trader.get_current_price("061040")  # 알에프텍
    if price_info:
        print(f"종목명: {price_info['stock_name']}")
        print(f"현재가: {price_info['current_price']:,}원")
        print(f"등락률: {price_info['change_rate']:+.2f}%")

    # 3. 매수 가능 수량 계산
    print("\n=== 2. 매수 가능 수량 계산 ===")
    quantity = trader.calculate_buy_quantity("061040")
    print(f"매수 가능한 수량: {quantity}주")

    # 4. 시장가 매수 (실제 실행시 주의!)
    print("\n=== 3. 시장가 매수 (주석 해제시 실행) ===")
    # buy_result = trader.smart_buy(stock_code="061040", buy_amount=trader.buy_amount)
    # print(buy_result)

    # 5. 포트폴리오 조회
    print("\n=== 4. 포트폴리오 조회 ===")
    portfolio = trader.get_portfolio()
    for stock in portfolio:
        print(f"{stock['stock_name']}({stock['stock_code']}): "
              f"{stock['quantity']}주, "
              f"평균단가: {stock['avg_price']:,.0f}원, "
              f"현재가: {stock['current_price']:,.0f}원, "
              f"수익률: {stock['profit_rate']:+.2f}%")

    # 6. 계좌 요약
    print("\n=== 5. 계좌 요약 ===")
    summary = trader.get_account_summary()
    if summary:
        print(f"총평가금액: {summary['total_eval_amount']:,.0f}원")
        print(f"총평가손익: {summary['total_profit_amount']:+,.0f}원")
        print(f"총수익률: {summary['total_profit_rate']:+.2f}%")
        print(f"주문가능금액: {summary['available_amount']:,.0f}원")

    # 7. 전량 매도 (실제 실행시 주의!)
    print("\n=== 6. 전량 매도 (주석 해제시 실행) ===")
    sell_result = trader.smart_sell_all("061040")
    print(sell_result)

# fixme : 아래 주석 삭제 예정
## 위 단위 기능들 테스트 성공(시장가 매수, 시간외 매도 테스트 필요) -> 매매 함수로 통합(ok) -> tracking_agent에 매매 함수 호출(ok) -> orchestrator에서 현재 계좌 현황 요약본 텔레그램 전송(테스트 필요)