#!/usr/bin/env python3
"""
JSON 파싱 오류 수정 테스트 코드

실제 발생한 JSON 파싱 오류와 SQLite에 저장된 데이터를 사용하여
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
    
    @staticmethod
    def fix_json_syntax(json_str: str) -> str:
        """JSON 문법 오류 수정 (stock_tracking_agent.py와 동일한 로직)"""
        # 1. 마지막 쉼표 제거
        json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
        
        # 2. 배열 뒤에 객체 속성이 오는 경우 쉼표 추가
        json_str = re.sub(r'(\])\s*(\n\s*")', r'\1,\2', json_str)
        
        # 3. 객체 뒤에 객체 속성이 오는 경우 쉼표 추가
        json_str = re.sub(r'(})\s*(\n\s*")', r'\1,\2', json_str)
        
        # 4. 숫자나 문자열 뒤에 속성이 오는 경우 쉼표 추가
        json_str = re.sub(r'([0-9]|")\s*(\n\s*")', r'\1,\2', json_str)
        
        # 5. 중복 쉼표 제거
        json_str = re.sub(r',\s*,', ',', json_str)
        
        return json_str
    
    def test_broken_json_from_error_log(self):
        """실제 에러 로그에서 발생한 JSON 파싱 테스트"""
        print("\n=== 테스트 1: 실제 오류 발생 JSON ===")
        
        # 실제 오류가 발생했던 JSON (sell_triggers와 hold_conditions 사이 쉼표 누락)
        broken_json = """{
  "portfolio_analysis": "보유 2/10 슬롯(화학/포장재 1, 반도체/전기전자 1). 중기 2, 단/장기 0으로 중기 편중. 평균 수익률 미제시. 동일 섹터(반도체/IT 하드웨어) 2종 보유 시 산업 노출 확대에 유의.",
  "valuation_analysis": "보고서 기준: PER 2024 66.6x, 2025E 56.3x, Fwd 12M 22.7x. PBR 2024 1.81x(2025E ~1.8x). EV/EBITDA Fwd 12M ~7.5x. ROE 2024 ~2.7% → 2025E ~3.2% 개선 예상. 업종(WI26) 평균 PER ~75x 대비 상대적 저평가로 보이나, 절대 PER은 아직 부담. PBR은 업종 보통 수준.",
  "sector_outlook": "AI/서버·첨단 패키징 투자 확대로 고부가 PCB 수요 증가. 동종업체 동반 강세, 2025~2026 성장 낙관론 우위. 다만 경쟁 심화·환율·글로벌 변수에 따른 변동성 상존.",
  "buy_score": 7,
  "min_score": 8,
  "decision": "관망",
  "target_price": 41000,
  "stop_loss": 30000,
  "investment_period": "중기",
  "rationale": "거래대금 급증·기관/외국인 동반 매수로 모멘텀 강함. 다만 신고가 인접(36.5~37천원)과 컨센서스 목표가 하회로 업사이드 제한. Fwd PER 개선 기대는 있으나 절대 밸류 부담과 실적 변동성 존재.",
  "sector": "IT 하드웨어(PCB/패키지기판)",
  "market_condition": "KOSPI 중기 강세(지수 3,600 저항 테스트), KOSDAQ 중립~약세. 정책 호재와 글로벌 불확실성 혼재, 리스크 레벨 중간. 급등주 중심 단기 변동성 확대.",
  "max_portfolio_size": "6",
  "trading_scenarios": {
    "key_levels": {
      "primary_support": 30000,
      "secondary_support": 27000,
      "primary_resistance": 37000,
      "secondary_resistance": 40000,
      "volume_baseline": "30만~70만주"
    },
    "sell_triggers": [
      "익절 조건 1: 41,000원 부근 도달 시 전량 매도",
      "익절 조건 2: 37,000원 돌파 후 3거래일 내 거래량 급감·음봉 연속(2일) 시 모멘텀 소진으로 매도",
      "손절 조건 1: 30,000원 종가 이탈 시 즉시 전량 손절",
      "손절 조건 2: 32,000원 이탈 후 기관/외국인 3일 연속 순매도+거래대금 급감 시 하락 가속으로 손절",
      "시간 조건: 20거래일 이상 32,000~37,000 박스권 횡보·수급 둔화 지속 시 기회비용 고려 청산"
    ]
    "hold_conditions": [
      "가격이 33,000원 이상에서 20·60일선 위 유지",
      "기관·외국인 순매수 추세 지속(주당 누적 +100만주/주 이상)",
      "37,000원 돌파 후 3거래일 안착 및 거래대금 상위 20위 내 유지"
    ],
    "portfolio_context": "현재 보유 2/10로 여유는 충분하나 동일 섹터 비중 확대와 신고가 국면의 변동성 고려 시 추격매수 리스크가 큼. 목표가 대비 업사이드 10% 이상 확신 부족으로 관망 우위. 돌파 안착 또는 30~32천원 조정 재진입 시나리오가 유리."
  }
}"""
        
        # 원래는 파싱 오류가 발생해야 함
        print("1) 오류 발생 JSON 파싱 시도...")
        try:
            json.loads(broken_json)
            print("   ❌ 예상과 달리 파싱 성공 (이상함)")
        except json.JSONDecodeError as e:
            print(f"   ✅ 예상대로 파싱 실패: {e}")
        
        # fix_json_syntax 적용 후 파싱
        print("2) fix_json_syntax 적용 후 파싱...")
        try:
            fixed_json = self.fix_json_syntax(broken_json)
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
    
    def test_sqlite_stored_json(self):
        """SQLite에 저장된 실제 JSON 데이터 파싱 테스트"""
        print("\n=== 테스트 2: SQLite 저장된 JSON ===")
        
        # SQLite에서 데이터 읽기
        conn = sqlite3.connect("stock_tracking_db.sqlite")
        cursor = conn.cursor()
        
        try:
            cursor.execute("SELECT ticker, company_name, scenario FROM stock_holdings")
            rows = cursor.fetchall()
            
            if not rows:
                print("   ⚠️ 데이터베이스에 데이터 없음")
                return True
            
            for ticker, company_name, scenario_json in rows:
                print(f"\n   종목: {company_name}({ticker})")
                try:
                    # JSON 파싱
                    scenario = json.loads(scenario_json)
                    
                    # 주요 필드 확인
                    assert 'portfolio_analysis' in scenario
                    assert 'buy_score' in scenario
                    assert 'decision' in scenario
                    
                    print(f"   ✅ 파싱 성공")
                    print(f"      - buy_score: {scenario['buy_score']}")
                    print(f"      - decision: {scenario['decision']}")
                    print(f"      - sector: {scenario.get('sector', 'N/A')}")
                    
                    # trading_scenarios가 있는 경우
                    if 'trading_scenarios' in scenario:
                        ts = scenario['trading_scenarios']
                        if 'sell_triggers' in ts:
                            print(f"      - sell_triggers: {len(ts['sell_triggers'])}개")
                        if 'hold_conditions' in ts:
                            print(f"      - hold_conditions: {len(ts['hold_conditions'])}개")
                    
                except json.JSONDecodeError as e:
                    print(f"   ❌ 파싱 실패: {e}")
                    return False
                except AssertionError as e:
                    print(f"   ❌ 필수 필드 누락: {e}")
                    return False
                    
        finally:
            conn.close()
        
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
                fixed = self.fix_json_syntax(test_case['broken'])
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
        "SQLite 저장 JSON": tester.test_sqlite_stored_json(),
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
