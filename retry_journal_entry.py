#!/usr/bin/env python3
"""
매매일지 재시도 스크립트
trading_history에서 데이터를 가져와 journal entry를 재생성합니다.

사용법:
    # 특정 거래 ID로 재시도
    python retry_journal_entry.py --id 40

    # 특정 종목코드로 최근 거래 재시도
    python retry_journal_entry.py --ticker 035720

    # 모든 journal 미생성 거래 재시도
    python retry_journal_entry.py --all-missing
"""

import argparse
import asyncio
import json
import logging
import sqlite3
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def retry_journal_entry(db_path: str, trade_id: int = None, ticker: str = None):
    """특정 거래에 대한 journal entry 재생성"""
    from stock_tracking_agent import StockTrackingAgent

    # DB에서 거래 정보 조회
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if trade_id:
        cursor.execute("SELECT * FROM trading_history WHERE id = ?", (trade_id,))
    elif ticker:
        cursor.execute(
            "SELECT * FROM trading_history WHERE ticker = ? ORDER BY sell_date DESC LIMIT 1",
            (ticker,)
        )
    else:
        logger.error("trade_id 또는 ticker를 지정해야 합니다")
        return False

    row = cursor.fetchone()
    if not row:
        logger.error(f"거래 기록을 찾을 수 없습니다 (id={trade_id}, ticker={ticker})")
        return False

    trade_data = dict(row)
    logger.info(f"거래 기록 조회 완료: {trade_data['company_name']}({trade_data['ticker']})")
    logger.info(f"  - 매수: {trade_data['buy_price']:,.0f}원 ({trade_data['buy_date']})")
    logger.info(f"  - 매도: {trade_data['sell_price']:,.0f}원 ({trade_data['sell_date']})")
    logger.info(f"  - 수익률: {trade_data['profit_rate']:.2f}%")

    # 이미 journal이 있는지 확인
    cursor.execute(
        """
        SELECT id FROM trading_journal
        WHERE ticker = ? AND trade_date LIKE ?
        """,
        (trade_data['ticker'], trade_data['sell_date'][:10] + '%')
    )
    existing = cursor.fetchone()
    if existing:
        logger.warning(f"이미 journal entry가 존재합니다 (journal_id={existing['id']})")
        confirm = input("덮어쓰시겠습니까? (y/N): ")
        if confirm.lower() != 'y':
            logger.info("취소되었습니다")
            return False
        # 기존 entry 삭제
        cursor.execute("DELETE FROM trading_journal WHERE id = ?", (existing['id'],))
        conn.commit()
        logger.info(f"기존 journal entry 삭제됨 (id={existing['id']})")

    conn.close()

    # StockTrackingAgent로 journal entry 생성
    agent = StockTrackingAgent(db_path=db_path, enable_journal=True)

    # stock_data 구성
    stock_data = {
        'ticker': trade_data['ticker'],
        'company_name': trade_data['company_name'],
        'buy_price': trade_data['buy_price'],
        'buy_date': trade_data['buy_date'],
        'scenario': trade_data['scenario'] or '{}'
    }

    # 매도 사유 추론 (scenario에서 추출 시도)
    sell_reason = "시스템 매도"
    try:
        scenario = json.loads(trade_data['scenario'] or '{}')
        if trade_data['profit_rate'] < 0:
            stop_loss = scenario.get('stop_loss')
            if stop_loss and trade_data['sell_price'] <= stop_loss:
                sell_reason = f"손절가({stop_loss:,.0f}원) 도달"
            else:
                sell_reason = "손실 청산"
        else:
            target_price = scenario.get('target_price')
            if target_price and trade_data['sell_price'] >= target_price:
                sell_reason = f"목표가({target_price:,.0f}원) 도달"
            else:
                sell_reason = "익절"
    except:
        pass

    logger.info(f"매도 사유: {sell_reason}")
    logger.info("Journal entry 생성 시작...")

    try:
        result = await agent._create_journal_entry(
            stock_data=stock_data,
            sell_price=trade_data['sell_price'],
            profit_rate=trade_data['profit_rate'],
            holding_days=trade_data['holding_days'],
            sell_reason=sell_reason
        )

        if result:
            logger.info("Journal entry 생성 완료!")
            return True
        else:
            logger.error("Journal entry 생성 실패")
            return False

    except Exception as e:
        logger.error(f"Journal entry 생성 중 오류: {e}")
        import traceback
        traceback.print_exc()
        return False


async def retry_all_missing(db_path: str):
    """journal이 없는 모든 거래에 대해 재시도"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # journal이 없는 거래 조회
    cursor.execute("""
        SELECT th.id, th.ticker, th.company_name, th.sell_date, th.profit_rate
        FROM trading_history th
        LEFT JOIN trading_journal tj ON th.ticker = tj.ticker
            AND date(th.sell_date) = date(tj.trade_date)
        WHERE tj.id IS NULL
        ORDER BY th.sell_date DESC
    """)

    missing = cursor.fetchall()
    conn.close()

    if not missing:
        logger.info("모든 거래에 journal이 존재합니다")
        return

    logger.info(f"Journal 미생성 거래: {len(missing)}건")
    for row in missing:
        logger.info(f"  - [{row['id']}] {row['company_name']}({row['ticker']}) "
                   f"{row['sell_date'][:10]} ({row['profit_rate']:.2f}%)")

    confirm = input(f"\n{len(missing)}건의 journal을 생성하시겠습니까? (y/N): ")
    if confirm.lower() != 'y':
        logger.info("취소되었습니다")
        return

    success = 0
    for row in missing:
        logger.info(f"\n{'='*50}")
        logger.info(f"처리 중: {row['company_name']}({row['ticker']})")
        result = await retry_journal_entry(db_path, trade_id=row['id'])
        if result:
            success += 1

    logger.info(f"\n완료: {success}/{len(missing)}건 성공")


def main():
    parser = argparse.ArgumentParser(description='매매일지 재시도 스크립트')
    parser.add_argument('--db-path', default='stock_tracking_db.sqlite',
                       help='데이터베이스 경로')
    parser.add_argument('--id', type=int, help='거래 ID')
    parser.add_argument('--ticker', help='종목코드 (최근 거래)')
    parser.add_argument('--all-missing', action='store_true',
                       help='journal 미생성 거래 모두 재시도')

    args = parser.parse_args()

    if args.all_missing:
        asyncio.run(retry_all_missing(args.db_path))
    elif args.id or args.ticker:
        asyncio.run(retry_journal_entry(args.db_path, trade_id=args.id, ticker=args.ticker))
    else:
        parser.print_help()
        print("\n예시:")
        print("  python retry_journal_entry.py --id 40")
        print("  python retry_journal_entry.py --ticker 035720")
        print("  python retry_journal_entry.py --all-missing")


if __name__ == "__main__":
    main()
