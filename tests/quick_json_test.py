#!/usr/bin/env python3
"""간단한 JSON 파싱 테스트"""

import json
import re

def fix_json_syntax(json_str):
    """JSON 문법 오류 수정"""
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

# 실제 에러가 발생했던 JSON (sell_triggers와 hold_conditions 사이 쉼표 누락)
broken_json = """{
  "sell_triggers": [
    "익절 조건 1: 41,000원 부근 도달 시 전량 매도",
    "익절 조건 2: 37,000원 돌파 후 3거래일 내 거래량 급감·음봉 연속(2일) 시 모멘텀 소진으로 매도"
  ]
  "hold_conditions": [
    "가격이 33,000원 이상에서 20·60일선 위 유지",
    "기관·외국인 순매수 추세 지속(주당 누적 +100만주/주 이상)"
  ]
}"""

print("=" * 50)
print("JSON 파싱 오류 수정 테스트")
print("=" * 50)

# 1. 원본 파싱 시도
print("\n1. 오류 JSON 직접 파싱:")
try:
    json.loads(broken_json)
    print("   ❌ 예상과 달리 파싱 성공")
except json.JSONDecodeError as e:
    print(f"   ✅ 예상대로 파싱 실패")
    print(f"      오류: {e}")

# 2. 수정 후 파싱
print("\n2. fix_json_syntax 적용 후:")
fixed_json = fix_json_syntax(broken_json)
print("   수정된 JSON:")
print("   " + "-" * 40)
# 수정된 부분만 출력
for line in fixed_json.split('\n')[2:5]:
    print(f"   {line}")
print("   " + "-" * 40)

try:
    parsed = json.loads(fixed_json)
    print("   ✅ 파싱 성공!")
    print(f"      - sell_triggers: {len(parsed['sell_triggers'])}개")
    print(f"      - hold_conditions: {len(parsed['hold_conditions'])}개")
except Exception as e:
    print(f"   ❌ 파싱 실패: {e}")

# 3. json-repair 테스트
print("\n3. json-repair 라이브러리 테스트:")
try:
    import json_repair
    repaired = json_repair.repair_json(broken_json)
    parsed = json.loads(repaired)
    print(f"   ✅ json-repair로 복구 성공!")
    print(f"      - sell_triggers: {len(parsed['sell_triggers'])}개")  
    print(f"      - hold_conditions: {len(parsed['hold_conditions'])}개")
except ImportError:
    print("   ⚠️ json-repair 미설치 (pip install json-repair)")
except Exception as e:
    print(f"   ❌ 복구 실패: {e}")

print("\n" + "=" * 50)
print("테스트 완료!")
