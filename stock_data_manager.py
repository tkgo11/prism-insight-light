# 종목 정보 관리를 위한 파일 구조 (stock_data_manager.py)
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from pykrx import stock

logger = logging.getLogger(__name__)

class StockDataManager:
    """종목 데이터 관리 클래스"""

    def __init__(self, data_dir="stock_data"):
        """초기화"""
        self.data_dir = Path(data_dir)
        self.data_file = self.data_dir / "stock_map.json"
        self.last_update_file = self.data_dir / "last_update.txt"
        self.stock_map = {}
        self.stock_name_map = {}

        # 데이터 디렉토리 생성
        os.makedirs(self.data_dir, exist_ok=True)

        # 데이터 로드
        self.load_stock_data()

    def load_stock_data(self):
        """저장된 종목 데이터 로드"""
        if self.data_file.exists():
            try:
                with open(self.data_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.stock_map = data["code_to_name"]
                    self.stock_name_map = data["name_to_code"]
                logger.info(f"{len(self.stock_map)} 개의 종목 정보 로드 완료")

                return len(self.stock_map) > 0
            except Exception as e:
                logger.error(f"종목 데이터 로드 실패: {e}")
                return False
        else:
            logger.warning("종목 데이터 파일이 존재하지 않음, 데이터 업데이트 필요")
            return False

    def update_stock_data(self):
        """pykrx를 사용하여 종목 데이터 업데이트"""
        try:
            logger.info("종목 데이터 업데이트 시작")

            # 오늘 날짜 형식 지정 (YYYYMMDD)
            today = datetime.now().strftime("%Y%m%d")

            # KOSPI 종목 정보 가져오기
            kospi_tickers = stock.get_market_ticker_list(market="KOSPI")
            kospi_map = {ticker: stock.get_market_ticker_name(ticker) for ticker in kospi_tickers}

            # KOSDAQ 종목 정보 가져오기
            kosdaq_tickers = stock.get_market_ticker_list(market="KOSDAQ")
            kosdaq_map = {ticker: stock.get_market_ticker_name(ticker) for ticker in kosdaq_tickers}

            # 결합
            self.stock_map = {**kospi_map, **kosdaq_map}
            self.stock_name_map = {v: k for k, v in self.stock_map.items()}

            # 데이터 저장
            data = {
                "code_to_name": self.stock_map,
                "name_to_code": self.stock_name_map,
                "updated_at": datetime.now().isoformat()
            }

            with open(self.data_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            # 마지막 업데이트 시간 기록
            with open(self.last_update_file, 'w', encoding='utf-8') as f:
                f.write(datetime.now().isoformat())

            logger.info(f"종목 데이터 업데이트 완료: {len(self.stock_map)} 개 종목")
            return True

        except Exception as e:
            logger.error(f"종목 데이터 업데이트 실패: {e}")
            return False

    def needs_update(self, days=1):
        """
        데이터 업데이트가 필요한지 확인
        Args:
            days (int): 업데이트 간격 (일)
        Returns:
            bool: 업데이트 필요 여부
        """
        if not self.last_update_file.exists():
            return True

        try:
            with open(self.last_update_file, 'r', encoding='utf-8') as f:
                last_update_str = f.read().strip()

            last_update = datetime.fromisoformat(last_update_str)
            days_since_update = (datetime.now() - last_update).days

            return days_since_update >= days
        except Exception:
            return True

    def get_stock_code(self, stock_input):
        """
        종목명 또는 코드를 입력받아 종목 코드와 이름으로 변환

        Args:
            stock_input (str): 종목 코드 또는 이름

        Returns:
            tuple: (종목 코드, 종목 이름, 오류 메시지)
        """
        # 이 부분은 telegram_ai_bot.py에 구현했던 get_stock_code 함수와 동일
        # 필요시 유사 종목명 검색 기능 확장 가능