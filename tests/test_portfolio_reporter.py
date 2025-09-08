#!/usr/bin/env python3
"""
포트폴리오 텔레그램 리포터 테스트 스크립트
"""

import asyncio
import os
import sys
import yaml
from pathlib import Path

# 현재 스크립트의 디렉토리를 기준으로 경로 설정
SCRIPT_DIR = Path(__file__).parent          # tests 디렉토리
PROJECT_ROOT = SCRIPT_DIR.parent             # 프로젝트 루트 (한 단계 위로)
TRADING_DIR = PROJECT_ROOT / "trading"

sys.path.insert(0, str(PROJECT_ROOT))       # 프로젝트 루트를 경로에 추가

# 설정파일 로딩
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
with open(CONFIG_FILE, encoding="UTF-8") as f:
    _cfg = yaml.load(f, Loader=yaml.FullLoader)

from trading.portfolio_telegram_reporter import PortfolioTelegramReporter


async def test_portfolio_reporter():
    """포트폴리오 리포터 테스트"""
    
    print("=== 포트폴리오 텔레그램 리포터 테스트 ===")
    print()
    
    # 환경변수 확인
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID")
    
    print("환경변수 확인:")
    print(f"TELEGRAM_BOT_TOKEN: {'✅ 설정됨' if telegram_token else '❌ 설정 안됨'}")
    print(f"TELEGRAM_CHANNEL_ID: {'✅ 설정됨' if chat_id else '❌ 설정 안됨'}")
    print()
    
    print("YAML 설정 확인:")
    print(f"기본 트레이딩 모드: {_cfg['default_mode']}")
    print(f"자동 트레이딩: {_cfg['auto_trading']}")
    print(f"설정 파일 경로: {CONFIG_FILE}")
    print()
    
    if not telegram_token or not chat_id:
        print("❌ 필수 환경변수가 설정되지 않았습니다.")
        print("다음과 같이 환경변수를 설정해주세요:")
        print("export TELEGRAM_BOT_TOKEN='your_bot_token'")
        print("export TELEGRAM_CHANNEL_ID='your_chat_id'")
        return False
    
    try:
        # 리포터 초기화 (yaml의 default_mode 사용)
        print("1️⃣ 리포터 초기화 중...")
        reporter = PortfolioTelegramReporter()  # trading_mode 파라미터 제거하면 yaml 설정 사용
        print(f"✅ 리포터 초기화 완료 (모드: {reporter.trading_mode})")
        print()
        
        # 트레이딩 데이터 조회 테스트
        print("2️⃣ 트레이딩 데이터 조회 테스트...")
        portfolio, account_summary = await reporter.get_trading_data()
        
        print(f"   포트폴리오 종목 수: {len(portfolio)}개")
        print(f"   계좌 요약 데이터: {'✅ 조회됨' if account_summary else '❌ 조회 실패'}")
        
        if account_summary:
            total_eval = account_summary.get('total_eval_amount', 0)
            total_profit = account_summary.get('total_profit_amount', 0)
            print(f"   총 평가금액: {total_eval:,.0f}원")
            print(f"   총 평가손익: {total_profit:+,.0f}원")
        print()
        
        # 메시지 생성 테스트
        print("3️⃣ 메시지 생성 테스트...")
        message = reporter.create_portfolio_message(portfolio, account_summary)
        print("✅ 메시지 생성 완료")
        print("--- 생성된 메시지 미리보기 ---")
        print(message[:500] + "..." if len(message) > 500 else message)
        print("--- 미리보기 끝 ---")
        print()
        
        # 사용자 확인
        print("4️⃣ 텔레그램 전송 테스트")
        response = input("실제로 텔레그램 메시지를 전송하시겠습니까? (y/N): ").strip().lower()
        
        if response in ['y', 'yes']:
            print("📤 텔레그램 메시지 전송 중...")
            success = await reporter.send_portfolio_report()
            
            if success:
                print("✅ 텔레그램 메시지 전송 성공!")
            else:
                print("❌ 텔레그램 메시지 전송 실패!")
                return False
        else:
            print("⏭️ 텔레그램 전송을 건너뜁니다.")
        
        print()
        print("🎉 모든 테스트가 완료되었습니다!")
        return True
        
    except Exception as e:
        print(f"❌ 테스트 중 오류 발생: {str(e)}")
        return False


async def test_simple_messages():
    """간단한 메시지들 테스트"""
    
    print("\n=== 간단한 메시지 테스트 ===")
    
    try:
        reporter = PortfolioTelegramReporter()  # yaml 설정 사용
        print(f"테스트 모드: {reporter.trading_mode}")
        
        # 다양한 메시지 타입 테스트
        message_types = ["morning", "evening", "market_close", "weekend"]
        
        for msg_type in message_types:
            response = input(f"{msg_type} 메시지를 전송하시겠습니까? (y/N): ").strip().lower()
            
            if response in ['y', 'yes']:
                print(f"📤 {msg_type} 메시지 전송 중...")
                success = await reporter.send_simple_status(msg_type)
                
                if success:
                    print(f"✅ {msg_type} 메시지 전송 성공!")
                else:
                    print(f"❌ {msg_type} 메시지 전송 실패!")
                
                print()
        
    except Exception as e:
        print(f"❌ 간단한 메시지 테스트 중 오류: {str(e)}")


async def test_both_modes():
    """두 모드 모두 테스트"""
    
    print("\n=== 양쪽 모드 테스트 ===")
    
    modes = ["demo", "real"]
    
    for mode in modes:
        response = input(f"{mode} 모드로 테스트하시겠습니까? (y/N): ").strip().lower()
        
        if response in ['y', 'yes']:
            try:
                print(f"📊 {mode} 모드 테스트 중...")
                reporter = PortfolioTelegramReporter(trading_mode=mode)  # 명시적으로 모드 지정
                
                portfolio, account_summary = await reporter.get_trading_data()
                print(f"   {mode} 모드 - 보유종목: {len(portfolio)}개")
                
                if account_summary:
                    total_eval = account_summary.get('total_eval_amount', 0)
                    print(f"   {mode} 모드 - 총평가: {total_eval:,.0f}원")
                
                # 전송 여부 확인
                send_response = input(f"{mode} 모드 리포트를 전송하시겠습니까? (y/N): ").strip().lower()
                if send_response in ['y', 'yes']:
                    success = await reporter.send_portfolio_report()
                    print(f"✅ {mode} 모드 전송 {'성공' if success else '실패'}!")
                
                print()
                
            except Exception as e:
                print(f"❌ {mode} 모드 테스트 중 오류: {str(e)}")


async def main():
    """메인 함수"""
    
    print("포트폴리오 텔레그램 리포터 테스트를 시작합니다.")
    print(f"프로젝트 루트: {PROJECT_ROOT}")
    print(f"설정 파일: {CONFIG_FILE}")
    print()
    
    # 기본 테스트 (yaml 설정 사용)
    success = await test_portfolio_reporter()
    
    if success:
        # 추가 테스트
        response = input("\n간단한 메시지들도 테스트하시겠습니까? (y/N): ").strip().lower()
        if response in ['y', 'yes']:
            await test_simple_messages()
        
        # 양쪽 모드 테스트
        response = input("\n양쪽 모드(demo/real) 모두 테스트하시겠습니까? (y/N): ").strip().lower()
        if response in ['y', 'yes']:
            await test_both_modes()
    
    print("\n테스트가 완료되었습니다.")


if __name__ == "__main__":
    asyncio.run(main())
