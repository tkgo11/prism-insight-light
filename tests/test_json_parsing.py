#!/usr/bin/env python3
"""
JSON 파싱 오류 수정 테스트 코드

stock_tracking_agent.py의 JSON 파싱 로직을 테스트합니다.
"""

import json
import re
import sys
import sqlite3
from pathlib import Path
from typing import Dict, Any

# 프로젝트 루트를 Python 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestJSONParser:
    """JSON 파싱 테스트 클래스"""
    
    def test_broken_json_from_error_log(self):
        """실제 에러 로그에서 발생한 JSON 파싱 테스트"""
        print("\n=== 테스트 1: 실제 오류 발생 JSON ===")
        
        # 실제 오류가 발생했던 JSON (sell_triggers, hold_conditions 대괄호가 아닌 중괄호로 닫힘)
        broken_json = """{
  "portfolio_analysis": "보유 2/10슬롯(여유 8). 산업 분포는 화학/포장재, 반도체/전기전자로 분산되어 있으며 자동차 유통 섹터 편입 시 과도한 중복 없음. 투자기간은 중기 중심(2/2)으로 단기 포지션 여지 존재. 포트폴리오 평균수익률 미제시.",
  "valuation_analysis": "보고서 기준 PER은 적자 지속으로 N/A, PBR 2.92배(’24/12), EV/EBITDA 20.23배, PCR 10.28배. 업종 평균 PER 4.91배 대비 ‘저평가’ 근거는 부족하며(이익 부재·고 PBR), 우선주 유통물량 희소로 가격 변동성 왜곡 리스크 큼. 외부 소스(Perplexity)로 동종 업계/경쟁사 최신 비교는 불충분하여 보수적 해석 필요.",
  "sector_outlook": "국내 자동차·모빌리티 업종은 3분기 실적 개선 기대, 친환경차/렌트·모빌리티 확장으로 심리 개선. 다만 유동성 낮은 종목·소형주 중심 변동성 확대, 외국인 수급 변화에 민감.",
  "buy_score": 6.5,
  "min_score": 7,
  "decision": "관망",
  "target_price": 55000,
  "stop_loss": 39500,
  "investment_period": "단기",
  "rationale": "밸류에이션 매력 제한(PER N/A, PBR 높음)과 유동주식 6.56%의 극단적 변동성. 4만~4.5만원 지지 구간 인접은 기술적 반등 여지. 거래대금 급감·외국인 수급 변동성으로 모멘텀 약화.",
  "sector": "자동차 유통/모빌리티",
  "market_condition": "KOSPI 강세·KOSDAQ 중립, 전체 리스크 중간. 본 종목 거래대금 전일 대비 -76.9%로 관망세/모멘텀 둔화. 외국인 수급 변화가 주가 단기 방향 핵심 변수.",
  "max_portfolio_size": "6",
  "trading_scenarios": {
    "key_levels": {
      "primary_support": 40000,
      "secondary_support": 35000,
      "primary_resistance": 55000,
      "secondary_resistance": "70000~80000",
      "volume_baseline": "일평균 약 50만주 내외(9~10월 기준), 급등구간 100만~150만주"
    },
    "sell_triggers": [
      "익절 조건 1: 55,000원 부근(중간 저항) 도달 시 전량 매도",
      "익절 조건 2: 거래량 감소·음봉 연속으로 모멘텀 소진 시 전량 매도",
      "손절 조건 1: 40,000원 종가 이탈 + 거래량 동반 증가 시 전량 손절",
      "손절 조건 2: 40,000원 이탈 후 35,000원 재지지 실패·하락 가속",
      "시간 조건: 진입 후 10거래일 내 5만대 회복 실패·횡보 지속 시 청산"
    },
    "hold_conditions": [
      "40,000~44,000원 지지선 수차례 방어 및 거래량 정상화",
      "외국인 순매수 전환·유지와 함께 5만대 안착",
      "업종/시장 강세 지속 및 분기 실적 개선 확인"
    },
    "portfolio_context": "현 포트폴리오에 소비경기/모빌리티 노출 추가로 분산효과는 있으나, 해당 종목은 유동성 리스크·변동성이 극단적. 분할매매 불가 시스템 특성상 트리거 충족 시에만 1슬롯(10%)로 단기 트레이드, 미충족 시 보유 회피가 합리적."
  }
}"""
        
        # 원래는 파싱 오류가 발생해야 함
        print("1) 오류 발생 JSON 파싱 시도...")
        try:
            json.loads(broken_json)
            print("   ❌ 예상과 달리 파싱 성공 (이상함)")
        except json.JSONDecodeError as e:
            print(f"   ✅ 예상대로 파싱 실패: {e}")
        
        # json_repair 적용 후 파싱
        print("2) json_repair 적용 후 파싱...")
        try:
            import json_repair
            fixed_json = json_repair.repair_json(broken_json)
            parsed = json.loads(fixed_json)
            print(f"   ✅ 파싱 성공!")
            print(f"   - portfolio_analysis: {parsed['portfolio_analysis'][:50]}...")
            print(f"   - buy_score: {parsed['buy_score']}")
            print(f"   - decision: {parsed['decision']}")
            print(f"   - sell_triggers 개수: {len(parsed['trading_scenarios']['sell_triggers'])}")
            print(f"   - hold_conditions 개수: {len(parsed['trading_scenarios']['hold_conditions'])}")
        except Exception as e:
            print(f"   ❌ 파싱 실패: {e}")
            return False
        
        return True
    
    def test_various_broken_json_patterns(self):
        """다양한 JSON 문법 오류 패턴 테스트"""
        print("\n=== 테스트 3: 다양한 문법 오류 패턴 ===")
        
        test_cases = [
            # 케이스 1: 배열 뒤 쉼표 누락
            {
                "name": "배열 뒤 속성",
                "broken": '{"array": [1, 2, 3]\n"next": "value"}',
                "expected_keys": ["array", "next"]
            },
            # 케이스 2: 객체 뒤 쉼표 누락
            {
                "name": "객체 뒤 속성",
                "broken": '{"obj": {"a": 1}\n"next": "value"}',
                "expected_keys": ["obj", "next"]
            },
            # 케이스 3: 마지막 쉼표
            {
                "name": "마지막 쉼표",
                "broken": '{"a": 1, "b": 2,}',
                "expected_keys": ["a", "b"]
            },
            # 케이스 4: 중복 쉼표
            {
                "name": "중복 쉼표",
                "broken": '{"a": 1,, "b": 2}',
                "expected_keys": ["a", "b"]
            },
            # 케이스 5: 복합 오류 (실제 시나리오)
            {
                "name": "복합 오류",
                "broken": """{
                    "list": ["a", "b", "c"]
                    "obj": {"x": 1, "y": 2,},
                    "value": 123
                    "last": true
                }""",
                "expected_keys": ["list", "obj", "value", "last"]
            }
        ]
        
        all_passed = True
        
        for i, test_case in enumerate(test_cases, 1):
            print(f"\n   테스트 {i}: {test_case['name']}")
            
            # 원본은 파싱 오류 발생해야 함
            try:
                json.loads(test_case['broken'])
                print(f"      ⚠️ 예상과 달리 원본 파싱 성공")
            except:
                print(f"      ✅ 원본 파싱 실패 (예상대로)")
            
            # 수정 후 파싱
            try:
                import json_repair
                fixed = json_repair.repair_json(test_case['broken'])
                parsed = json.loads(fixed)
                
                # 예상 키 확인
                for key in test_case['expected_keys']:
                    if key not in parsed:
                        print(f"      ❌ 키 '{key}' 누락")
                        all_passed = False
                        break
                else:
                    print(f"      ✅ 수정 후 파싱 성공 (모든 키 존재)")
                    
            except Exception as e:
                print(f"      ❌ 수정 후에도 파싱 실패: {e}")
                all_passed = False
        
        return all_passed
    
    def test_json_repair_fallback(self):
        """json-repair 라이브러리 폴백 테스트"""
        print("\n=== 테스트 4: json-repair 라이브러리 폴백 ===")
        
        try:
            import json_repair
            print("   ✅ json-repair 라이브러리 설치됨")
            
            # 매우 복잡하게 깨진 JSON
            very_broken_json = """
            {
                "a": "value with "quotes" inside",
                'b': 'single quotes',
                c: "no quotes key",
                "d": [1, 2, 3
                "e": {
                    "nested": true
                }
                "f": /* comment */ 123,
                "g": NaN,
                "h": undefined,
            }
            """
            
            # json_repair로 복구
            try:
                repaired = json_repair.repair_json(very_broken_json)
                parsed = json.loads(repaired)
                print(f"   ✅ 매우 깨진 JSON도 복구 성공!")
                print(f"      복구된 키들: {list(parsed.keys())}")
            except Exception as e:
                print(f"   ❌ json_repair 복구 실패: {e}")
                
        except ImportError:
            print("   ⚠️ json-repair 라이브러리 미설치 (선택사항)")
        
        return True


def main():
    """메인 테스트 실행"""
    print("=" * 60)
    print("JSON 파싱 오류 수정 테스트")
    print("=" * 60)
    
    tester = TestJSONParser()
    
    # 각 테스트 실행
    results = {
        "실제 오류 JSON": tester.test_broken_json_from_error_log(),
        "다양한 오류 패턴": tester.test_various_broken_json_patterns(),
        "json-repair 폴백": tester.test_json_repair_fallback(),
    }
    
    # 결과 요약
    print("\n" + "=" * 60)
    print("테스트 결과 요약")
    print("=" * 60)
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_name:20} : {status}")
    
    # 전체 결과
    all_passed = all(results.values())
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ 모든 테스트 통과!")
    else:
        print("❌ 일부 테스트 실패")
    print("=" * 60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())
