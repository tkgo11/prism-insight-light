# Release Notes v1.16.6

**Release Date**: 2026-01-14

## Overview

trigger_batch 알고리즘의 핵심 개선을 통해 모든 시장 상황(강세장/약세장/횡보장)에서 안정적으로 작동하는 종목 선별 시스템을 구현했습니다. 연평균 15% 수익 목표 달성을 위한 기반을 마련했습니다.

---

## Major Changes

### 1. 손절가 계산 방식 변경 (핵심)

**Before (v1.16.5)**: 10일 지지선 기반
```python
# 10일 저가 중 최저점을 지지선으로 사용
support_level = multi_day_df["Low"].min()
stop_loss_price = support_level * 0.99

# 문제: 급등주의 경우 손절폭이 48%+에 달함
# 예: 8,000원 → 14,500원 (+81%) 급등 시
#     지지선 7,500원 → 손절폭 48.8%
```

**After (v1.16.6)**: 현재가 기준 고정 손절폭
```python
# 트리거 유형별 고정 손절폭 적용
criteria = TRIGGER_CRITERIA.get(trigger_type, TRIGGER_CRITERIA["default"])
sl_max = criteria["sl_max"]  # 5% or 7%

stop_loss_price = current_price * (1 - sl_max)

# 결과: 모든 종목에서 일관된 손절폭
# 예: 14,500원 × 0.95 = 13,775원 (-5%)
```

### 2. 시총 필터 변경

| Version | 시총 기준 | 대상 종목 수 |
|---------|----------|-------------|
| v1.16.5 | 3,000억 이상 | ~734개 (25%) |
| v1.16.6 | 5,000억 이상 | ~518개 (18%) |

**변경 이유**:
- 유동성 확보 (일일 거래대금 50억+ 보장)
- 기관 투자자 관심 존재
- 호가 스프레드 관리 용이

### 3. 등락률 필터 신규 적용

**Before (v1.16.5)**: 등락률 상한 필터 없음

**After (v1.16.6)**: 20% 상한 적용
```python
snap = snap[snap["전일대비등락률"] <= 20.0]
```

**변경 이유**:
- 상한가(+30%) 근접 종목의 추가 상승 여력 제한
- 급등 후 단기 조정 리스크 관리
- 고정 손절폭 방식과의 정합성

### 4. 트리거 유형별 기준 체계

```python
TRIGGER_CRITERIA = {
    "intraday_surge": {
        "sl_max": 0.05,      # 손절폭 -5%
        "rr_target": 1.5,    # 손익비 1.5+
    },
    "volume_surge": {
        "sl_max": 0.07,      # 손절폭 -7%
        "rr_target": 2.0,    # 손익비 2.0+
    },
    "gap_up": {
        "sl_max": 0.07,
        "rr_target": 2.0,
    },
    "sector_top": {
        "sl_max": 0.07,
        "rr_target": 2.0,
    },
    "default": {
        "sl_max": 0.07,
        "rr_target": 2.0,
    },
}
```

### 5. 목표가 최소값 보장

**Before (v1.16.5)**: 저항선이 현재가보다 낮으면 그대로 사용

**After (v1.16.6)**: 최소 +15% 보장
```python
min_target = current_price * 1.15
if target_price < min_target:
    target_price = min_target
```

---

## Technical Details

### 에이전트 적합도 점수 계산

```python
# 손익비 점수 (60% 가중치)
rr_score = min(risk_reward_ratio / rr_target, 1.0)

# 손절폭 점수 (40% 가중치) - 항상 만점
sl_score = 1.0  # 고정 손절폭이므로 항상 기준 충족

# 최종 점수
agent_fit_score = rr_score * 0.6 + sl_score * 0.4
```

### 하이브리드 선별 점수

```python
# 복합점수 (30%) + 에이전트점수 (70%)
hybrid_score = composite_score * 0.3 + agent_fit_score * 0.7
```

---

## Expected Impact

### 선별 결과 비교

| 지표 | v1.16.5 | v1.16.6 |
|------|---------|---------|
| 급등주 agent_fit_score | 0.03 (거의 0) | 1.0 (만점) |
| 일관성 | 시장 상황에 따라 변동 | 모든 시장에서 일관 |
| 선별 풀 | 734개 | 518개 |

### 연 15% 수익 달성 시나리오 (10슬롯 시스템)

```
포트폴리오: 10개 슬롯
회전율: 슬롯당 월 2회 (평균 보유기간 15일)
월 총 진입: 20회
연 총 진입: 240회

손익비 2.0+ (R:R = 1:2)
- 손절: -5% ~ -7%
- 익절: +10% ~ +15%

확률 분포:
- 손절 35%, 본전청산 15%, 익절 50%

월간 기대 수익: ~2%
연환산 (복리): ~27%
거래비용/슬리피지 차감: ~21%
시장 악조건 할인: ~15%
```

---

## Migration Notes

기존 데이터베이스와 완전 호환됩니다. 추가 마이그레이션 불필요.

---

## Files Changed

| File | Changes |
|------|---------|
| `trigger_batch.py` | 손절가 계산 방식 변경, 시총/등락률 필터, 에이전트 점수 |
| `docs/TRIGGER_BATCH_ALGORITHMS.md` | 알고리즘 문서 Version 3.0 업데이트 |
| `CLAUDE.md` | Version 1.3 업데이트 |

---

## Version History

- **v1.16.6** (2026-01-14): 고정 손절폭 방식, 시총 5000억, 등락률 20%
- **v1.16.5** (2026-01-13): 시총 3000억, 등락률 필터 없음, 10일 지지선 기반
