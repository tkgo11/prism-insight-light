#!/usr/bin/env python3
"""
Migrate watchlist_history to analysis_performance_tracker

기존 watchlist_history 데이터를 analysis_performance_tracker 테이블로 마이그레이션합니다.
- 7/14/30일 후 가격 데이터를 pykrx에서 조회하여 수익률 계산
- trading_history와 비교하여 was_traded 필드 설정
- trigger_type이 없는 경우 기본값 사용

Usage:
    python utils/migrate_watchlist_to_performance_tracker.py --dry-run  # 미리보기
    python utils/migrate_watchlist_to_performance_tracker.py            # 실행
"""

import argparse
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# pykrx import (optional - for price data)
try:
    from pykrx import stock as pykrx_stock
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False
    logger.warning("pykrx not available. Price tracking will be skipped.")


def get_db_path() -> Path:
    """Get database path"""
    # Try relative path first
    db_path = Path("stock_tracking_db.sqlite")
    if db_path.exists():
        return db_path

    # Try from project root
    project_root = Path(__file__).parent.parent
    db_path = project_root / "stock_tracking_db.sqlite"
    if db_path.exists():
        return db_path

    raise FileNotFoundError("Database not found")


def get_traded_tickers(conn: sqlite3.Connection) -> dict:
    """
    trading_history에서 매매한 종목 목록 조회
    Returns: {ticker: [buy_dates]}
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ticker, buy_date
        FROM trading_history
        ORDER BY buy_date
    """)

    traded = {}
    for ticker, buy_date in cursor.fetchall():
        if ticker not in traded:
            traded[ticker] = []
        # Extract date part only
        if buy_date:
            date_part = buy_date.split()[0] if ' ' in buy_date else buy_date[:10]
            traded[ticker].append(date_part)

    return traded


def get_price_on_date(ticker: str, target_date: str) -> float | None:
    """
    특정 날짜의 종가 조회 (pykrx 사용)
    target_date: YYYY-MM-DD format
    """
    if not PYKRX_AVAILABLE:
        return None

    try:
        # Format date for pykrx (YYYYMMDD)
        date_formatted = target_date.replace("-", "")

        # Get OHLCV data for the date range (in case of holidays)
        start_date = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y%m%d")
        end_date = (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=5)).strftime("%Y%m%d")

        df = pykrx_stock.get_market_ohlcv(start_date, end_date, ticker)

        if df.empty:
            return None

        # Find closest date
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        for idx in df.index:
            if idx.date() >= target_dt.date():
                return float(df.loc[idx, '종가'])

        # If no future date, use last available
        return float(df.iloc[-1]['종가'])

    except Exception as e:
        logger.debug(f"Failed to get price for {ticker} on {target_date}: {e}")
        return None


def calculate_tracking_data(analyzed_date: str, analyzed_price: float, ticker: str) -> dict:
    """
    7/14/30일 후 가격 및 수익률 계산
    """
    result = {
        'tracked_7d_date': None, 'tracked_7d_price': None, 'tracked_7d_return': None,
        'tracked_14d_date': None, 'tracked_14d_price': None, 'tracked_14d_return': None,
        'tracked_30d_date': None, 'tracked_30d_price': None, 'tracked_30d_return': None,
        'tracking_status': 'pending'
    }

    if not PYKRX_AVAILABLE or not analyzed_price or analyzed_price <= 0:
        return result

    try:
        # Parse analyzed date
        if ' ' in analyzed_date:
            base_date = datetime.strptime(analyzed_date.split()[0], "%Y-%m-%d")
        else:
            base_date = datetime.strptime(analyzed_date[:10], "%Y-%m-%d")

        today = datetime.now()
        days_passed = (today - base_date).days

        # 7-day tracking
        if days_passed >= 7:
            target_date = (base_date + timedelta(days=7)).strftime("%Y-%m-%d")
            price = get_price_on_date(ticker, target_date)
            if price:
                result['tracked_7d_date'] = target_date
                result['tracked_7d_price'] = price
                result['tracked_7d_return'] = ((price - analyzed_price) / analyzed_price) * 100

        # 14-day tracking
        if days_passed >= 14:
            target_date = (base_date + timedelta(days=14)).strftime("%Y-%m-%d")
            price = get_price_on_date(ticker, target_date)
            if price:
                result['tracked_14d_date'] = target_date
                result['tracked_14d_price'] = price
                result['tracked_14d_return'] = ((price - analyzed_price) / analyzed_price) * 100

        # 30-day tracking
        if days_passed >= 30:
            target_date = (base_date + timedelta(days=30)).strftime("%Y-%m-%d")
            price = get_price_on_date(ticker, target_date)
            if price:
                result['tracked_30d_date'] = target_date
                result['tracked_30d_price'] = price
                result['tracked_30d_return'] = ((price - analyzed_price) / analyzed_price) * 100

        # Determine tracking status
        if result['tracked_30d_return'] is not None:
            result['tracking_status'] = 'completed'
        elif result['tracked_7d_return'] is not None:
            result['tracking_status'] = 'in_progress'
        else:
            result['tracking_status'] = 'pending'

    except Exception as e:
        logger.warning(f"Error calculating tracking data for {ticker}: {e}")

    return result


def determine_trigger_type(record: dict) -> str:
    """
    기존 데이터에서 trigger_type 결정
    """
    # Use existing trigger_type if available
    if record.get('trigger_type') and record['trigger_type'].strip():
        return record['trigger_type']

    # Otherwise, derive from context
    rationale = record.get('rationale', '') or ''
    skip_reason = record.get('skip_reason', '') or ''

    # Heuristics based on rationale/skip_reason
    combined = (rationale + ' ' + skip_reason).lower()

    if '급등' in combined or 'surge' in combined or '거래량' in combined:
        return '거래량 급증'
    elif '갭' in combined or 'gap' in combined:
        return '갭 상승'
    elif '돌파' in combined or 'breakout' in combined:
        return '기술적 돌파'
    elif '뉴스' in combined or 'news' in combined:
        return '뉴스 촉발'
    else:
        return '종합 분석'  # Default


def determine_trigger_mode(analyzed_date: str) -> str:
    """
    분석 시간에서 trigger_mode 결정 (morning/afternoon)
    """
    try:
        if ' ' in analyzed_date:
            time_part = analyzed_date.split()[1]
            hour = int(time_part.split(':')[0])
            return 'morning' if hour < 12 else 'afternoon'
    except:
        pass
    return 'afternoon'  # Default


def check_was_traded(ticker: str, analyzed_date: str, traded_tickers: dict) -> int:
    """
    해당 분석 후 실제 매매 여부 확인
    """
    if ticker not in traded_tickers:
        return 0

    try:
        analyzed_dt = analyzed_date.split()[0] if ' ' in analyzed_date else analyzed_date[:10]

        for buy_date in traded_tickers[ticker]:
            # If bought within 3 days after analysis, consider it as traded
            analyzed = datetime.strptime(analyzed_dt, "%Y-%m-%d")
            bought = datetime.strptime(buy_date, "%Y-%m-%d")

            if 0 <= (bought - analyzed).days <= 3:
                return 1
    except:
        pass

    return 0


def migrate_data(conn: sqlite3.Connection, dry_run: bool = True) -> dict:
    """
    watchlist_history → analysis_performance_tracker 마이그레이션
    """
    cursor = conn.cursor()

    # Get existing records to avoid duplicates
    cursor.execute("SELECT watchlist_id FROM analysis_performance_tracker")
    existing_ids = {row[0] for row in cursor.fetchall()}

    # Get traded tickers
    traded_tickers = get_traded_tickers(conn)
    logger.info(f"Found {len(traded_tickers)} traded tickers in trading_history")

    # Get all watchlist records
    cursor.execute("""
        SELECT
            id, ticker, company_name, current_price, analyzed_date,
            buy_score, min_score, decision, skip_reason,
            target_price, stop_loss, risk_reward_ratio,
            trigger_type, trigger_mode, was_traded,
            rationale
        FROM watchlist_history
        ORDER BY analyzed_date
    """)

    records = cursor.fetchall()
    logger.info(f"Found {len(records)} records in watchlist_history")

    stats = {
        'total': len(records),
        'skipped_existing': 0,
        'migrated': 0,
        'with_7d': 0,
        'with_14d': 0,
        'with_30d': 0,
        'traded': 0,
        'watched': 0
    }

    for row in records:
        record = {
            'id': row[0],
            'ticker': row[1],
            'company_name': row[2],
            'current_price': row[3],
            'analyzed_date': row[4],
            'buy_score': row[5],
            'min_score': row[6],
            'decision': row[7],
            'skip_reason': row[8],
            'target_price': row[9],
            'stop_loss': row[10],
            'risk_reward_ratio': row[11],
            'trigger_type': row[12],
            'trigger_mode': row[13],
            'was_traded': row[14],
            'rationale': row[15]
        }

        # Skip if already migrated
        if record['id'] in existing_ids:
            stats['skipped_existing'] += 1
            continue

        # Determine trigger info
        trigger_type = determine_trigger_type(record)
        trigger_mode = record.get('trigger_mode') or determine_trigger_mode(record['analyzed_date'])

        # Check if actually traded
        was_traded = record.get('was_traded', 0)
        if not was_traded:
            was_traded = check_was_traded(record['ticker'], record['analyzed_date'], traded_tickers)

        if was_traded:
            stats['traded'] += 1
        else:
            stats['watched'] += 1

        # Calculate tracking data
        tracking = calculate_tracking_data(
            record['analyzed_date'],
            record['current_price'],
            record['ticker']
        )

        if tracking['tracked_7d_return'] is not None:
            stats['with_7d'] += 1
        if tracking['tracked_14d_return'] is not None:
            stats['with_14d'] += 1
        if tracking['tracked_30d_return'] is not None:
            stats['with_30d'] += 1

        if dry_run:
            logger.info(f"[DRY-RUN] Would migrate: {record['ticker']} ({record['analyzed_date']}) "
                       f"trigger={trigger_type}, traded={was_traded}, status={tracking['tracking_status']}")
        else:
            # Insert into analysis_performance_tracker
            cursor.execute("""
                INSERT INTO analysis_performance_tracker (
                    watchlist_id, ticker, company_name,
                    trigger_type, trigger_mode,
                    analyzed_date, analyzed_price,
                    decision, was_traded, skip_reason,
                    buy_score, min_score,
                    target_price, stop_loss, risk_reward_ratio,
                    tracked_7d_date, tracked_7d_price, tracked_7d_return,
                    tracked_14d_date, tracked_14d_price, tracked_14d_return,
                    tracked_30d_date, tracked_30d_price, tracked_30d_return,
                    tracking_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record['id'], record['ticker'], record['company_name'],
                trigger_type, trigger_mode,
                record['analyzed_date'], record['current_price'],
                record['decision'], was_traded, record['skip_reason'],
                record['buy_score'], record['min_score'],
                record['target_price'], record['stop_loss'], record['risk_reward_ratio'],
                tracking['tracked_7d_date'], tracking['tracked_7d_price'], tracking['tracked_7d_return'],
                tracking['tracked_14d_date'], tracking['tracked_14d_price'], tracking['tracked_14d_return'],
                tracking['tracked_30d_date'], tracking['tracked_30d_price'], tracking['tracked_30d_return'],
                tracking['tracking_status'],
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))

        stats['migrated'] += 1

    if not dry_run:
        conn.commit()

    return stats


def main():
    parser = argparse.ArgumentParser(description='Migrate watchlist to performance tracker')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
    parser.add_argument('--db', type=str, help='Database path (optional)')
    args = parser.parse_args()

    # Get database path
    try:
        db_path = Path(args.db) if args.db else get_db_path()
    except FileNotFoundError as e:
        logger.error(f"Database not found: {e}")
        return

    logger.info(f"Using database: {db_path}")

    if args.dry_run:
        logger.info("=== DRY RUN MODE (no changes will be made) ===")

    # Connect and migrate
    conn = sqlite3.connect(db_path)

    try:
        stats = migrate_data(conn, dry_run=args.dry_run)

        print("\n" + "=" * 60)
        print("Migration Summary")
        print("=" * 60)
        print(f"Total records in watchlist_history: {stats['total']}")
        print(f"Already migrated (skipped):         {stats['skipped_existing']}")
        print(f"Records to migrate:                 {stats['migrated']}")
        print(f"  - Traded:                         {stats['traded']}")
        print(f"  - Watched:                        {stats['watched']}")
        print(f"  - With 7-day data:                {stats['with_7d']}")
        print(f"  - With 14-day data:               {stats['with_14d']}")
        print(f"  - With 30-day data (completed):   {stats['with_30d']}")
        print("=" * 60)

        if args.dry_run:
            print("\nTo execute migration, run without --dry-run flag")
        else:
            print(f"\n✓ Successfully migrated {stats['migrated']} records")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
