"""
비동기 트레이딩 API 테스트 스크립트

주의사항:
- 이 테스트는 모의투자 환경에서만 실행하세요
- 실제 매매가 발생할 수 있으므로 종목코드와 금액을 신중히 설정하세요
- 테스트 전 config/kis_devlp.yaml 파일의 설정을 확인하세요
"""

import asyncio
import sys
import os
from pathlib import Path
import logging
from typing import List, Dict, Any

# 상위 디렉토리의 trading 모듈 import를 위한 경로 설정
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from trading.domestic_stock_trading import DomesticStockTrading, AsyncTradingContext

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AsyncTradingTester:
    """비동기 트레이딩 API 테스터"""
    
    def __init__(self, mode: str = "demo", buy_amount: int = 100000):
        """
        초기화
        
        Args:
            mode: "demo" (모의투자) 또는 "real" (실전투자) - 테스트는 반드시 demo로!
            buy_amount: 매수 금액 (테스트용이므로 소액으로 설정)
        """
        if mode != "demo" and mode != "real":
            raise ValueError("mode는 'demo' 또는 'real'이어야 합니다!")
            
        self.mode = mode
        self.buy_amount = buy_amount
        logger.info(f"테스터 초기화: 모드={mode}, 매수금액={buy_amount:,}원")

    async def test_single_buy(self, stock_code: str = "061040") -> Dict[str, Any]:
        """단일 매수 테스트"""
        logger.info(f"=== 단일 매수 테스트 시작: {stock_code} ===")
        
        async with AsyncTradingContext(self.mode, self.buy_amount) as trader:
            try:
                result = await trader.async_buy_stock(stock_code, timeout=30.0)
                
                if result['success']:
                    logger.info(f"✅ 매수 성공: {result['message']}")
                else:
                    logger.warning(f"❌ 매수 실패: {result['message']}")
                
                return result
                
            except Exception as e:
                logger.error(f"단일 매수 테스트 중 오류: {e}")
                return {'success': False, 'error': str(e)}

    async def test_single_sell(self, stock_code: str = "061040") -> Dict[str, Any]:
        """단일 매도 테스트"""
        logger.info(f"=== 단일 매도 테스트 시작: {stock_code} ===")
        
        async with AsyncTradingContext(self.mode, self.buy_amount) as trader:
            try:
                result = await trader.async_sell_stock(stock_code, timeout=30.0)
                
                if result['success']:
                    logger.info(f"✅ 매도 성공: {result['message']}")
                else:
                    logger.warning(f"❌ 매도 실패: {result['message']}")
                
                return result
                
            except Exception as e:
                logger.error(f"단일 매도 테스트 중 오류: {e}")
                return {'success': False, 'error': str(e)}

    async def test_portfolio_check(self) -> Dict[str, Any]:
        """포트폴리오 조회 테스트"""
        logger.info("=== 포트폴리오 조회 테스트 시작 ===")
        
        async with AsyncTradingContext(self.mode, self.buy_amount) as trader:
            try:
                # 포트폴리오 조회
                portfolio = await asyncio.to_thread(trader.get_portfolio)
                summary = await asyncio.to_thread(trader.get_account_summary)
                
                logger.info(f"📊 보유 종목 수: {len(portfolio)}개")
                
                if summary:
                    logger.info(f"💰 총평가금액: {summary.get('total_eval_amount', 0):,.0f}원")
                    logger.info(f"📈 총평가손익: {summary.get('total_profit_amount', 0):+,.0f}원")
                
                # 보유 종목 상세 출력
                for i, stock in enumerate(portfolio[:5]):  # 최대 5개만 표시
                    logger.info(f"  {i+1}. {stock['stock_name']}({stock['stock_code']}): "
                              f"{stock['quantity']}주, 수익률: {stock['profit_rate']:+.2f}%")
                
                return {
                    'success': True,
                    'portfolio_count': len(portfolio),
                    'portfolio': portfolio,
                    'summary': summary
                }
                
            except Exception as e:
                logger.error(f"포트폴리오 조회 테스트 중 오류: {e}")
                return {'success': False, 'error': str(e)}

    async def test_batch_operations(self, stock_codes: List[str] = None) -> Dict[str, Any]:
        """배치 매매 테스트"""
        if stock_codes is None:
            stock_codes = ["061040", "100130"]  # 알에프텍, 동국S&C (소량 테스트)
        
        logger.info(f"=== 배치 매매 테스트 시작: {stock_codes} ===")
        
        async with AsyncTradingContext(self.mode, self.buy_amount) as trader:
            try:
                # 1단계: 배치 매수
                logger.info("🔄 배치 매수 실행...")
                buy_tasks = [
                    trader.async_buy_stock(code, timeout=45.0)
                    for code in stock_codes
                ]
                
                buy_results = await asyncio.gather(*buy_tasks, return_exceptions=True)
                
                # 매수 결과 분석
                successful_buys = []
                for i, result in enumerate(buy_results):
                    if isinstance(result, Exception):
                        logger.error(f"[{stock_codes[i]}] 매수 중 예외: {result}")
                    elif result.get('success'):
                        successful_buys.append(result)
                        logger.info(f"[{result['stock_code']}] 매수 성공")
                    else:
                        logger.warning(f"[{stock_codes[i]}] 매수 실패: {result.get('message', '알 수 없는 오류')}")
                
                logger.info(f"✅ 배치 매수 완료: {len(successful_buys)}개 성공")
                
                # 2단계: 잠시 대기
                if successful_buys:
                    logger.info("⏰ 3초 대기...")
                    await asyncio.sleep(3)
                    
                    # 3단계: 배치 매도
                    logger.info("🔄 배치 매도 실행...")
                    successful_codes = [r['stock_code'] for r in successful_buys]
                    
                    sell_tasks = [
                        trader.async_sell_stock(code, timeout=45.0)
                        for code in successful_codes
                    ]
                    
                    sell_results = await asyncio.gather(*sell_tasks, return_exceptions=True)
                    
                    # 매도 결과 분석
                    successful_sells = []
                    for i, result in enumerate(sell_results):
                        if isinstance(result, Exception):
                            logger.error(f"[{successful_codes[i]}] 매도 중 예외: {result}")
                        elif result.get('success'):
                            successful_sells.append(result)
                            logger.info(f"[{result['stock_code']}] 매도 성공")
                        else:
                            logger.warning(f"[{successful_codes[i]}] 매도 실패: {result.get('message', '알 수 없는 오류')}")
                    
                    logger.info(f"✅ 배치 매도 완료: {len(successful_sells)}개 성공")
                
                return {
                    'success': True,
                    'buy_results': buy_results,
                    'sell_results': sell_results if successful_buys else [],
                    'summary': {
                        'total_requested': len(stock_codes),
                        'buy_success': len(successful_buys),
                        'sell_success': len(successful_sells) if successful_buys else 0
                    }
                }
                
            except Exception as e:
                logger.error(f"배치 매매 테스트 중 오류: {e}")
                return {'success': False, 'error': str(e)}

    async def test_error_handling(self) -> Dict[str, Any]:
        """에러 처리 테스트"""
        logger.info("=== 에러 처리 테스트 시작 ===")
        
        async with AsyncTradingContext(self.mode, self.buy_amount) as trader:
            results = {}
            
            # 1. 잘못된 종목코드 매수 테스트
            logger.info("🧪 잘못된 종목코드 매수 테스트...")
            try:
                invalid_result = await trader.async_buy_stock("999999", timeout=10.0)
                results['invalid_buy'] = invalid_result
                logger.info(f"잘못된 종목코드 결과: {invalid_result['message']}")
            except Exception as e:
                results['invalid_buy'] = {'error': str(e)}
                logger.error(f"잘못된 종목코드 테스트 오류: {e}")
            
            # 2. 보유하지 않은 종목 매도 테스트
            logger.info("🧪 보유하지 않은 종목 매도 테스트...")
            try:
                no_holding_result = await trader.async_sell_stock("005490", timeout=10.0)  # 포스코홀딩스
                results['no_holding_sell'] = no_holding_result
                logger.info(f"보유하지 않은 종목 매도 결과: {no_holding_result['message']}")
            except Exception as e:
                results['no_holding_sell'] = {'error': str(e)}
                logger.error(f"보유하지 않은 종목 매도 테스트 오류: {e}")
            
            # 3. 타임아웃 테스트 (매우 짧은 타임아웃)
            logger.info("🧪 타임아웃 테스트...")
            try:
                timeout_result = await trader.async_buy_stock("061040", timeout=0.001)  # 1ms 타임아웃
                results['timeout_test'] = timeout_result
                logger.info(f"타임아웃 테스트 결과: {timeout_result['message']}")
            except Exception as e:
                results['timeout_test'] = {'error': str(e)}
                logger.error(f"타임아웃 테스트 오류: {e}")
            
            return {'success': True, 'tests': results}

    async def run_basic_tests(self, mode: str = None) -> Dict[str, Any]:
        """
        기본 테스트 실행 (클래스 메서드)
        
        Args:
            mode: 테스트 모드 (None이면 초기화 시 설정한 모드 사용)
        """
        # mode 파라미터가 주어지면 사용, 아니면 인스턴스의 mode 사용
        test_mode = mode if mode is not None else self.mode
        
        if test_mode == "real":
            logger.warning("⚠️ 실전투자 모드로 테스트를 실행합니다!")
            confirmation = input("정말 실전투자로 테스트하시겠습니까? (yes/no): ")
            if confirmation.lower() != "yes":
                return {"success": False, "message": "사용자가 실전투자 테스트를 취소했습니다."}
        
        logger.info(f"🚀 비동기 트레이딩 API 기본 테스트 시작 (모드: {test_mode})")
        
        results = {}
        
        try:
            # 테스트용 tester 생성 (mode 파라미터 사용)
            test_tester = AsyncTradingTester(mode=test_mode, buy_amount=self.buy_amount)
            
            # 1. 포트폴리오 조회 테스트
            portfolio_result = await test_tester.test_portfolio_check()
            results['portfolio'] = portfolio_result
            print(f"\n1️⃣ 포트폴리오 조회: {'성공' if portfolio_result['success'] else '실패'}")
            
            # 2. 단일 매수 테스트
            buy_result = await test_tester.test_single_buy("061040")
            results['buy'] = buy_result
            print(f"\n2️⃣ 단일 매수: {'성공' if buy_result['success'] else '실패'}")
            
            if buy_result['success']:
                # 3. 단일 매도 테스트
                await asyncio.sleep(2)
                sell_result = await test_tester.test_single_sell("061040")
                results['sell'] = sell_result
                print(f"\n3️⃣ 단일 매도: {'성공' if sell_result['success'] else '실패'}")
            
            # 4. 에러 처리 테스트
            error_result = await test_tester.test_error_handling()
            results['error_handling'] = error_result
            print(f"\n4️⃣ 에러 처리 테스트: {'성공' if error_result['success'] else '실패'}")
            
            results['success'] = True
            results['test_mode'] = test_mode
            
        except Exception as e:
            logger.error(f"기본 테스트 실행 중 오류: {e}")
            results['success'] = False
            results['error'] = str(e)
        
        logger.info("✅ 기본 테스트 완료")
        return results

    async def run_batch_tests(self, mode: str = None) -> Dict[str, Any]:
        """
        배치 테스트 실행 (클래스 메서드)
        
        Args:
            mode: 테스트 모드 (None이면 초기화 시 설정한 모드 사용)
        """
        test_mode = mode if mode is not None else self.mode
        
        if test_mode == "real":
            logger.warning("⚠️ 실전투자 모드로 배치 테스트를 실행합니다!")
            confirmation = input("정말 실전투자로 배치 테스트하시겠습니까? (yes/no): ")
            if confirmation.lower() != "yes":
                return {"success": False, "message": "사용자가 실전투자 배치 테스트를 취소했습니다."}
        
        logger.info(f"🚀 비동기 트레이딩 API 배치 테스트 시작 (모드: {test_mode})")
        
        try:
            # 테스트용 tester 생성
            test_tester = AsyncTradingTester(mode=test_mode, buy_amount=30000)  # 배치는 더 소액
            
            # 배치 매매 테스트
            batch_result = await test_tester.test_batch_operations(["061040", "100130"])
            print(f"\n🔄 배치 매매 테스트: {'성공' if batch_result['success'] else '실패'}")
            
            if batch_result['success']:
                summary = batch_result['summary']
                print(f"   - 요청: {summary['total_requested']}개")
                print(f"   - 매수 성공: {summary['buy_success']}개")
                print(f"   - 매도 성공: {summary['sell_success']}개")
            
            batch_result['test_mode'] = test_mode
            return batch_result
            
        except Exception as e:
            logger.error(f"배치 테스트 실행 중 오류: {e}")
            return {"success": False, "error": str(e), "test_mode": test_mode}


async def main():
    """메인 테스트 함수"""
    print("="*60)
    print("🧪 비동기 트레이딩 API 테스트 스크립트")
    print("="*60)
    print("⚠️  주의: 실전투자 모드 선택 시 실제 매매가 발생합니다!")
    print("="*60)
    
    try:
        # 모드 선택
        print("\n투자 모드를 선택하세요:")
        print("1. 모의투자 (demo) - 안전한 테스트")
        print("2. 실전투자 (real) - ⚠️ 실제 매매 발생!")
        
        mode_choice = input("모드 선택 (1-2): ").strip()
        
        if mode_choice == "1":
            mode = "demo"
            print("✅ 모의투자 모드 선택")
        elif mode_choice == "2":
            mode = "real"
            print("⚠️ 실전투자 모드 선택 - 신중히 진행하세요!")
        else:
            print("올바른 모드를 선택해주세요.")
            return
        
        # 테스터 생성
        tester = AsyncTradingTester(mode=mode, buy_amount=10000)
        
        # 테스트 옵션 선택
        print("\n테스트 옵션을 선택하세요:")
        print("1. 기본 테스트 (포트폴리오 조회, 단일 매수/매도, 에러 처리)")
        print("2. 배치 테스트 (여러 종목 동시 매수/매도)")
        print("3. 모든 테스트")
        print("4. 종료")
        
        choice = input("\n선택 (1-4): ").strip()
        
        if choice == "1":
            await tester.run_basic_tests()
        elif choice == "2":
            await tester.run_batch_tests()
        elif choice == "3":
            await tester.run_basic_tests()
            print("\n" + "="*40)
            await tester.run_batch_tests()
        elif choice == "4":
            print("테스트를 종료합니다.")
            return
        else:
            print("올바른 선택지를 입력해주세요.")
            return
            
    except KeyboardInterrupt:
        print("\n\n🛑 사용자에 의해 테스트가 중단되었습니다.")
    except Exception as e:
        logger.error(f"메인 테스트 실행 중 오류: {e}")
    
    print("\n" + "="*60)
    print("✅ 테스트 스크립트 종료")
    print("="*60)


if __name__ == "__main__":
    # 이벤트 루프 실행
    asyncio.run(main())
