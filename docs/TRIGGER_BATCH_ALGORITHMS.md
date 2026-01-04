# Trigger Batch 알고리즘 문서

> **Last Updated**: 2026-01-05
> **File**: `trigger_batch.py`
> **Purpose**: 급등주/모멘텀 종목 자동 스크리닝

---

## 목차

1. [개요](#1-개요)
2. [공통 필터](#2-공통-필터)
3. [오전 트리거 (Morning)](#3-오전-트리거-morning)
4. [오후 트리거 (Afternoon)](#4-오후-트리거-afternoon)
5. [복합 점수 계산](#5-복합-점수-계산)
6. [사용법](#6-사용법)

---

## 1. 개요

### 목적

`trigger_batch.py`는 매일 오전/오후에 실행되어 **관심 종목 후보**를 자동으로 선별합니다. 선별된 종목은 이후 AI 분석 파이프라인(`stock_analysis_orchestrator.py`)으로 전달됩니다.

### 실행 흐름

```
trigger_batch.py (종목 스크리닝)
    ↓
stock_analysis_orchestrator.py (AI 분석)
    ↓
stock_tracking_agent.py (매수/매도 결정)
    ↓
trading/domestic_stock_trading.py (실제 주문)
```

### 데이터 소스

- **kospi_kosdaq_stock_server**: KRX 정보데이터시스템 API
- **스냅샷 데이터**: OHLCV (시가, 고가, 저가, 종가, 거래량, 거래대금)
- **시가총액 데이터**: 종목별 시가총액

---

## 2. 공통 필터

모든 트리거에 적용되는 기본 필터입니다.

### 2.1 절대적 기준 필터 (`apply_absolute_filters`)

| 필터 | 기준 | 목적 |
|------|------|------|
| 최소 거래대금 | 5억원 이상 | 유동성 확보 |
| 최소 거래량 | 시장 평균의 20% 이상 | 거래 활성화 종목 |

```python
def apply_absolute_filters(df, min_value=500000000):
    filtered = df[df['거래대금'] >= min_value]
    avg_volume = df['거래량'].mean()
    filtered = filtered[filtered['거래량'] >= avg_volume * 0.2]
    return filtered
```

### 2.2 시가총액 필터

| 필터 | 기준 | 목적 |
|------|------|------|
| 최소 시가총액 | 500억원 이상 | 동전주/소형주 제외 |

### 2.3 저유동성 필터 (`filter_low_liquidity`)

거래량 하위 N% 종목 제외 (기본값: 20%)

---

## 3. 오전 트리거 (Morning)

오전 배치는 **장 시작 후** 실행되며, 3개 트리거에서 각 1개씩 총 3개 종목을 선별합니다.

### 3.1 거래량 급증 상위주 (`trigger_morning_volume_surge`)

**목적**: 전일 대비 거래량이 급증한 종목 포착

#### 선별 조건

| 조건 | 기준 |
|------|------|
| 거래량 증가율 | 전일 대비 30% 이상 |
| 상승 여부 | 시가 대비 현재가 상승 |
| 거래대금 | 5억원 이상 |
| 시가총액 | 500억원 이상 |

#### 복합 점수

```
복합점수 = 거래량증가율(60%) + 절대거래량(40%)
```

#### 로직 흐름

```
1. 전일 대비 거래량 비율 계산
2. 거래량 30%+ 증가 종목 필터링
3. 복합 점수 계산 및 상위 N개 선정
4. 상승세 종목만 최종 선별 (2차 필터)
5. 상위 3개 반환
```

---

### 3.2 갭 상승 모멘텀 상위주 (`trigger_morning_gap_up_momentum`)

**목적**: 갭 상승으로 시작한 모멘텀 종목 포착

#### 선별 조건

| 조건 | 기준 |
|------|------|
| 갭상승률 | 전일 종가 대비 1% 이상 |
| 상승 지속 | 현재가 > 시가 |
| 거래대금 | 5억원 이상 |
| 시가총액 | 500억원 이상 |

#### 복합 점수

```
복합점수 = 갭상승률(50%) + 장중등락률(30%) + 거래대금(20%)
```

---

### 3.3 시총 대비 집중 자금 유입 상위주 (`trigger_morning_value_to_cap_ratio`)

**목적**: 시가총액 대비 비정상적으로 높은 거래대금이 유입된 종목 포착

#### 선별 조건

| 조건 | 기준 |
|------|------|
| 거래대금비율 | 거래대금 / 시가총액 |
| 상승 여부 | 시가 대비 현재가 상승 |
| 거래대금 | 5억원 이상 |
| 시가총액 | 500억원 이상 |

#### 복합 점수

```
복합점수 = 거래대금비율(50%) + 절대거래대금(30%) + 장중등락률(20%)
```

---

## 4. 오후 트리거 (Afternoon)

오후 배치는 **장 마감 후** 실행되며, 3개 트리거에서 각 1개씩 총 3개 종목을 선별합니다.

### 4.1 일중 상승률 상위주 (`trigger_afternoon_daily_rise_top`)

**목적**: 당일 가장 강하게 상승한 종목 포착

#### 선별 조건

| 조건 | 기준 |
|------|------|
| 장중등락률 | 시가 대비 3% 이상 상승 |
| 거래대금 | 10억원 이상 (강화) |
| 시가총액 | 500억원 이상 |

#### 복합 점수

```
복합점수 = 장중등락률(60%) + 거래대금(40%)
```

---

### 4.2 마감 강도 상위주 (`trigger_afternoon_closing_strength`)

**목적**: 종가가 고가에 가까운(강한 마감) 종목 포착

#### 선별 조건

| 조건 | 기준 |
|------|------|
| 마감 강도 | (종가 - 저가) / (고가 - 저가) |
| 거래량 증가 | 전일 대비 거래량 증가 |
| 상승 여부 | 시가 대비 종가 상승 |
| 거래대금 | 5억원 이상 |
| 시가총액 | 500억원 이상 |

#### 마감 강도 계산

```python
마감강도 = (종가 - 저가) / (고가 - 저가)
# 1에 가까울수록 강한 마감 (종가 ≈ 고가)
# 0에 가까울수록 약한 마감 (종가 ≈ 저가)
```

#### 복합 점수

```
복합점수 = 마감강도(50%) + 거래량증가율(30%) + 거래대금(20%)
```

---

### 4.3 거래량 증가 상위 횡보주 (`trigger_afternoon_volume_surge_flat`)

**목적**: 거래량은 급증했지만 가격은 횡보하는 종목 포착 (세력 매집 의심)

#### 선별 조건

| 조건 | 기준 |
|------|------|
| 거래량 증가율 | 전일 대비 50% 이상 |
| 횡보 여부 | 장중등락률 ±5% 이내 |
| 거래대금 | 5억원 이상 |
| 시가총액 | 500억원 이상 |

#### 복합 점수

```
복합점수 = 거래량증가율(60%) + 거래대금(40%)
```

---

## 5. 복합 점수 계산

### 정규화 방식

모든 지표는 0~1 사이로 정규화됩니다:

```python
normalized = (value - min) / (max - min)
```

### 가중치 적용

```python
복합점수 = Σ (정규화된_지표 × 가중치)
```

### 최종 선별 로직 (`select_final_tickers`)

1. 각 트리거에서 최상위 종목 1개씩 선택 (최대 3개)
2. 3개 미만이면 전체 복합 점수 순으로 추가
3. 중복 종목 제거

---

## 6. 사용법

### 기본 실행

```bash
# 오전 배치
python trigger_batch.py morning INFO

# 오후 배치
python trigger_batch.py afternoon INFO
```

### 옵션

```bash
# JSON 결과 저장
python trigger_batch.py morning INFO --output result.json

# 디버그 모드
python trigger_batch.py morning DEBUG
```

### 출력 예시 (JSON)

```json
{
  "거래량 급증 상위주": [
    {
      "code": "005930",
      "name": "삼성전자",
      "current_price": 75000,
      "change_rate": 3.5,
      "volume": 15000000,
      "trade_value": 1125000000000,
      "volume_increase": 150.5
    }
  ],
  "갭 상승 모멘텀 상위주": [
    {
      "code": "000660",
      "name": "SK하이닉스",
      "current_price": 180000,
      "change_rate": 2.8,
      "gap_rate": 1.5
    }
  ],
  "metadata": {
    "run_time": "2026-01-05T10:30:00",
    "trigger_mode": "morning",
    "trade_date": "20260102"
  }
}
```

---

## 부록: 함수 목록

| 함수명 | 카테고리 | 설명 |
|--------|----------|------|
| `get_snapshot` | 데이터 | 당일 OHLCV 조회 |
| `get_previous_snapshot` | 데이터 | 전일 OHLCV 조회 |
| `get_market_cap_df` | 데이터 | 시가총액 조회 |
| `apply_absolute_filters` | 필터 | 절대적 기준 필터 |
| `filter_low_liquidity` | 필터 | 저유동성 필터 |
| `normalize_and_score` | 점수 | 정규화 및 복합점수 |
| `enhance_dataframe` | 유틸 | 종목명/업종 추가 |
| `trigger_morning_volume_surge` | 오전 | 거래량 급증 |
| `trigger_morning_gap_up_momentum` | 오전 | 갭상승 모멘텀 |
| `trigger_morning_value_to_cap_ratio` | 오전 | 시총 대비 자금유입 |
| `trigger_afternoon_daily_rise_top` | 오후 | 일중 상승률 |
| `trigger_afternoon_closing_strength` | 오후 | 마감 강도 |
| `trigger_afternoon_volume_surge_flat` | 오후 | 거래량 증가 횡보 |
| `select_final_tickers` | 선별 | 최종 종목 선택 |
| `run_batch` | 실행 | 배치 실행 |

---

**Document Version**: 2.0
**Author**: PRISM-INSIGHT Development Team
