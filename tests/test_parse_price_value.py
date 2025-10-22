#!/usr/bin/env python3
"""
_parse_price_value 함수 테스트 스크립트

이 테스트는 stock_tracking_agent.py의 _parse_price_value 메서드를 다양한 입력 조건에서 검증합니다.
"""

import sys
import re
from pathlib import Path

# 프로젝트 루트를 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestParsePriceValue:
    """_parse_price_value 함수 테스트를 위한 독립 클래스"""
    
    @staticmethod
    def _parse_price_value(value) -> float:
        """
        가격 값을 파싱하여 숫자로 변환
        (stock_tracking_agent.py의 메서드와 동일한 로직)
        
        Args:
            value: 가격 값 (숫자, 문자열, 범위 등)
            
        Returns:
            float: 파싱된 가격 (실패 시 0)
        """
        try:
            # 이미 숫자인 경우
            if isinstance(value, (int, float)):
                return float(value)
            
            # 문자열인 경우
            if isinstance(value, str):
                # 쉼표 제거
                value = value.replace(',', '')
                
                # 범위 표현 체크 (예: "2000~2050", "1,700-1,800")
                range_patterns = [
                    r'(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)',  # 2000~2050 or 2000-2050
                    r'(\d+(?:\.\d+)?)\s*~\s*(\d+(?:\.\d+)?)',     # 2000 ~ 2050
                ]
                
                for pattern in range_patterns:
                    match = re.search(pattern, value)
                    if match:
                        # 범위의 중간값 사용
                        low = float(match.group(1))
                        high = float(match.group(2))
                        return (low + high) / 2
                
                # 단일 숫자 추출 시도
                number_match = re.search(r'(\d+(?:\.\d+)?)', value)
                if number_match:
                    return float(number_match.group(1))
            
            return 0
        except Exception as e:
            print(f"⚠️  가격 값 파싱 실패: {value} - {str(e)}")
            return 0


def run_tests():
    """모든 테스트 케이스 실행"""
    
    tester = TestParsePriceValue()
    
    # 테스트 케이스 정의
    test_cases = [
        # (입력값, 예상 출력, 설명)
        
        # 1. 숫자 타입 테스트
        (2000, 2000.0, "정수 입력"),
        (2000.5, 2000.5, "실수 입력"),
        (0, 0.0, "0 입력"),
        (-1500, -1500.0, "음수 입력"),
        
        # 2. 문자열 숫자 테스트
        ("2000", 2000.0, "문자열 정수"),
        ("2000.5", 2000.5, "문자열 실수"),
        ("1,700", 1700.0, "쉼표 포함 문자열"),
        ("1,700.5", 1700.5, "쉼표 + 소수점 문자열"),
        ("10,000", 10000.0, "큰 숫자 쉼표 표기"),
        
        # 3. 범위 표현 테스트 (틸드 ~)
        ("2000~2050", 2025.0, "틸드 범위 (공백 없음)"),
        ("2000 ~ 2050", 2025.0, "틸드 범위 (공백 있음)"),
        ("1,700~1,800", 1750.0, "쉼표 + 틸드 범위"),
        ("1,350~1,400", 1375.0, "쉼표 + 틸드 범위 2"),
        
        # 4. 범위 표현 테스트 (하이픈 -)
        ("2000-2050", 2025.0, "하이픈 범위"),
        ("1,700-1,800", 1750.0, "쉼표 + 하이픈 범위"),
        
        # 5. 실제 에러 케이스에서 발견된 패턴
        ("2,000~2,050", 2025.0, "실제 에러 케이스 1"),
        ("2,400~2,500", 2450.0, "실제 에러 케이스 2"),
        ("1,350~1,400", 1375.0, "실제 에러 케이스 3"),
        ("1,700", 1700.0, "실제 에러 케이스 4 (단일값)"),
        
        # 6. 소수점 포함 범위
        ("1500.5~1600.5", 1550.5, "소수점 포함 범위"),
        ("1,500.25~1,600.75", 1550.5, "쉼표 + 소수점 범위"),
        
        # 7. 공백이 많은 경우
        ("2000  ~  2050", 2025.0, "공백 많은 범위"),
        ("  1700  ", 1700.0, "앞뒤 공백"),
        
        # 8. 특수 케이스
        ("", 0.0, "빈 문자열"),
        (None, 0.0, "None 입력"),
        ("abc", 0.0, "숫자가 없는 문자열"),
        ("price: 2000", 2000.0, "텍스트 포함 (숫자 추출)"),
        ("약 1,700원", 1700.0, "한글 포함 (숫자 추출)"),
        
        # 9. 복잡한 패턴
        ("1,700원~2,000원", 1850.0, "단위 포함 범위"),
        ("최소 1,500 ~ 최대 2,000", 1750.0, "설명 포함 범위"),
    ]
    
    # 테스트 실행
    print("=" * 80)
    print("_parse_price_value 함수 테스트")
    print("=" * 80)
    print()
    
    passed = 0
    failed = 0
    
    for i, (input_value, expected, description) in enumerate(test_cases, 1):
        result = tester._parse_price_value(input_value)
        
        # 부동소수점 비교를 위한 허용 오차
        tolerance = 0.01
        is_correct = abs(result - expected) < tolerance
        
        if is_correct:
            status = "✅ PASS"
            passed += 1
        else:
            status = "❌ FAIL"
            failed += 1
        
        print(f"테스트 #{i}: {status}")
        print(f"  설명: {description}")
        print(f"  입력: {repr(input_value)}")
        print(f"  예상: {expected}")
        print(f"  결과: {result}")
        
        if not is_correct:
            print(f"  ⚠️  차이: {abs(result - expected)}")
        
        print()
    
    # 결과 요약
    print("=" * 80)
    print("테스트 결과 요약")
    print("=" * 80)
    print(f"총 테스트: {len(test_cases)}개")
    print(f"✅ 성공: {passed}개")
    print(f"❌ 실패: {failed}개")
    print(f"성공률: {(passed / len(test_cases) * 100):.1f}%")
    print("=" * 80)
    
    # 실패한 경우 종료 코드 반환
    return 0 if failed == 0 else 1


def run_edge_case_tests():
    """엣지 케이스 추가 테스트"""
    
    print("\n\n")
    print("=" * 80)
    print("엣지 케이스 추가 테스트")
    print("=" * 80)
    print()
    
    tester = TestParsePriceValue()
    
    edge_cases = [
        # 매우 큰 숫자
        ("1,000,000", 1000000.0, "백만 단위"),
        ("1,000,000~2,000,000", 1500000.0, "백만 단위 범위"),
        
        # 매우 작은 숫자
        ("0.001", 0.001, "매우 작은 소수"),
        ("0.001~0.002", 0.0015, "매우 작은 소수 범위"),
        
        # 여러 개의 숫자 (첫 번째만 추출)
        ("1,700 또는 2,000", 1700.0, "여러 숫자 중 첫 번째"),
        
        # 범위가 역순인 경우 (큰 수 ~ 작은 수)
        ("2000~1500", 1750.0, "역순 범위"),
        
        # 음수 범위
        ("-1000~-500", -750.0, "음수 범위"),
        
        # 혼합된 구분자
        ("1,700 - 1,800", 1750.0, "쉼표 + 하이픈 (공백 포함)"),
    ]
    
    for i, (input_value, expected, description) in enumerate(edge_cases, 1):
        result = tester._parse_price_value(input_value)
        tolerance = 0.01
        is_correct = abs(result - expected) < tolerance
        
        status = "✅ PASS" if is_correct else "❌ FAIL"
        
        print(f"엣지 케이스 #{i}: {status}")
        print(f"  설명: {description}")
        print(f"  입력: {repr(input_value)}")
        print(f"  예상: {expected}")
        print(f"  결과: {result}")
        print()


def performance_test():
    """성능 테스트"""
    import time
    
    print("\n\n")
    print("=" * 80)
    print("성능 테스트")
    print("=" * 80)
    print()
    
    tester = TestParsePriceValue()
    
    # 다양한 입력 패턴
    test_inputs = [
        2000,
        "2,000",
        "2,000~2,050",
        "1,700-1,800",
        "약 1,500원",
    ]
    
    iterations = 10000
    
    for input_value in test_inputs:
        start_time = time.time()
        
        for _ in range(iterations):
            tester._parse_price_value(input_value)
        
        elapsed_time = time.time() - start_time
        avg_time = (elapsed_time / iterations) * 1000  # 밀리초 단위
        
        print(f"입력: {repr(input_value)}")
        print(f"  {iterations:,}회 반복 시간: {elapsed_time:.4f}초")
        print(f"  평균 실행 시간: {avg_time:.6f}ms")
        print()


if __name__ == "__main__":
    # 기본 테스트 실행
    exit_code = run_tests()
    
    # 엣지 케이스 테스트
    run_edge_case_tests()
    
    # 성능 테스트
    performance_test()
    
    sys.exit(exit_code)
