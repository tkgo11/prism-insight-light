#!/usr/bin/env python3
from holidays.countries import KR
from datetime import date
import sys
import logging

# 로깅 설정
logging.basicConfig(
    filename='/root/kospi-kosdaq-stock-analyzer/stock_scheduler.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

def is_market_day():
    """한국 주식 시장 영업일인지 확인"""
    today = date.today()

    # 주말 체크 (5:토요일, 6:일요일)
    if today.weekday() >= 5:
        return False

    # 한국 공휴일 체크
    kr_holidays = KR()
    if today in kr_holidays:
        return False

    # 영업일
    return True

if __name__ == "__main__":
    if is_market_day():
        # 영업일이면 종료 코드 0 (정상)
        sys.exit(0)
    else:
        # 영업일이 아니면 종료 코드 1 (비정상)
        sys.exit(1)