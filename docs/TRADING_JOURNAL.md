# Trading Journal System

매매 일지 시스템은 완료된 거래를 AI가 복기하고, 시간이 지남에 따라 기억을 계층적으로 압축하여 장기적인 매매 직관(Intuition)을 축적하는 시스템입니다.

## 개요

### 핵심 개념

```
매매 완료 → 복기 분석 → 일지 저장 → 시간 경과 → 기억 압축 → 직관 추출
```

- **Trading Journal (매매 일지)**: 매 거래 종료 시 AI가 매수/매도 맥락을 비교 분석하고 교훈을 추출
- **Memory Compression (기억 압축)**: 시간이 지난 일지를 단계별로 요약하여 저장 공간과 조회 효율 최적화
- **Trading Intuitions (매매 직관)**: 반복되는 패턴에서 추출된 규칙으로, 새로운 매수 결정에 참고

### 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────┐
│                     Stock Tracking Agent                         │
│                    (stock_tracking_agent.py)                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌───────────────────┐    ┌──────────────┐ │
│  │   매도 실행   │───>│ write_trading_    │───>│trading_journal│ │
│  │              │    │ journal()         │    │    테이블     │ │
│  └──────────────┘    └───────────────────┘    └──────────────┘ │
│         │                    │                       │          │
│         │                    ▼                       │          │
│         │           ┌───────────────────┐            │          │
│         │           │ trading_journal_  │            │          │
│         │           │ agent.py          │            │          │
│         │           │ (AI 복기 분석)     │            │          │
│         │           └───────────────────┘            │          │
│         │                                            │          │
│         │    ┌───────────────────────────────────────┘          │
│         │    │                                                  │
│         ▼    ▼                                                  │
│  ┌──────────────────┐    ┌───────────────────┐                 │
│  │ compress_old_    │───>│ memory_compressor_│                 │
│  │ journal_entries()│    │ agent.py          │                 │
│  └──────────────────┘    │ (AI 압축/직관추출) │                 │
│         │                └───────────────────┘                 │
│         │                        │                              │
│         ▼                        ▼                              │
│  ┌──────────────┐         ┌──────────────┐                     │
│  │ 압축된 일지   │         │trading_      │                     │
│  │ (Layer 2,3)  │         │intuitions    │                     │
│  └──────────────┘         │   테이블      │                     │
│                           └──────────────┘                     │
└─────────────────────────────────────────────────────────────────┘
```

## 구성 요소

### 1. Trading Journal Agent (`cores/agents/trading_journal_agent.py`)

매도 완료 후 호출되어 거래를 복기 분석합니다.

#### 주요 기능
- 매수 시점 vs 매도 시점 상황 비교 분석
- 판단의 적절성 평가 (매수/매도 타이밍)
- 실행 가능한 교훈 추출
- 패턴 태그 부여 (미래 검색용)

#### 응답 형식
```json
{
    "situation_analysis": {
        "buy_context_summary": "매수 당시 상황 요약",
        "sell_context_summary": "매도 당시 상황 요약",
        "key_changes": ["주요 변화 1", "주요 변화 2"]
    },
    "judgment_evaluation": {
        "buy_quality": "적절/부적절/보통",
        "sell_quality": "적절/조급/지연/보통",
        "missed_signals": ["놓친 신호들"],
        "overreacted_signals": ["과민 반응한 신호들"]
    },
    "lessons": [
        {
            "condition": "이런 상황에서는...",
            "action": "이렇게 해야 한다...",
            "reason": "왜냐하면...",
            "priority": "high/medium/low"
        }
    ],
    "pattern_tags": ["급등후조정", "손절지연"],
    "one_line_summary": "한 줄 요약",
    "confidence_score": 0.8
}
```

#### 패턴 태그 예시

| 카테고리 | 태그 예시 |
|---------|----------|
| 시장 관련 | `강세장진입`, `약세장손절`, `횡보장관망` |
| 종목 관련 | `급등후조정`, `박스권돌파`, `거래량급감`, `지지선반등` |
| 실수 관련 | `손절지연`, `익절조급`, `재료과신`, `추격매수`, `패닉매도` |
| 성공 관련 | `추세추종`, `눌림목매수`, `원칙준수`, `적정비중` |

### 2. Memory Compressor Agent (`cores/agents/memory_compressor_agent.py`)

시간이 지난 일지를 계층적으로 압축합니다.

#### 압축 레이어

| Layer | 기간 | 내용 |
|-------|------|------|
| Layer 1 | 0-7일 | 상세 기록 (원본 그대로) |
| Layer 2 | 8-30일 | 요약 형식 `"{섹터} + {트리거} → {행동} → {결과}"` |
| Layer 3 | 31일+ | 직관 형식 `"{조건} = {원칙}"` + 통계 |

#### Layer 2 요약 형식 예시
```
"반도체 급등 + 거래량 감소 → 익절 → 수익 +5%"
"바이오 테마 + 뉴스 과열 → 관망 → 조정 피함"
```

#### Layer 3 직관 형식 예시
```
"거래량 급감 3일 = 추세 전환 신호 (적중률 72%, n=18)"
"급등 후 2일내 5% 추가 상승 = 과열 경고 (적중률 65%, n=12)"
```

### 3. Compression Script (`compress_trading_memory.py`)

주기적으로 압축을 실행하는 CLI 스크립트입니다.

#### 사용법

```bash
# 기본 실행
python compress_trading_memory.py

# 드라이런 (변경 없이 확인만)
python compress_trading_memory.py --dry-run

# 커스텀 설정
python compress_trading_memory.py --layer1-age 7 --layer2-age 30

# 강제 실행 (최소 항목 수 무시)
python compress_trading_memory.py --force
```

#### 옵션

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--db-path` | `stock_tracking_db.sqlite` | 데이터베이스 경로 |
| `--layer1-age` | 7 | Layer 1 → 2 압축 기준 일수 |
| `--layer2-age` | 30 | Layer 2 → 3 압축 기준 일수 |
| `--min-entries` | 3 | 압축 실행 최소 항목 수 |
| `--dry-run` | - | 변경 없이 확인만 |
| `--force` | - | 최소 항목 수 무시 |
| `--language` | ko | 에이전트 언어 (ko/en) |

#### Cron 설정 (권장)

```bash
# 매주 일요일 새벽 3시에 실행
0 3 * * 0 cd /path/to/prism-insight && python compress_trading_memory.py >> logs/compression.log 2>&1
```

## 데이터베이스 스키마

### trading_journal 테이블

```sql
CREATE TABLE trading_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 거래 기본 정보
    ticker TEXT NOT NULL,
    company_name TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    trade_type TEXT NOT NULL,  -- 'buy' or 'sell'

    -- 매수 맥락
    buy_price REAL,
    buy_date TEXT,
    buy_scenario TEXT,         -- JSON: 매수 시나리오
    buy_market_context TEXT,   -- JSON: 매수 시 시장 상황

    -- 매도 맥락
    sell_price REAL,
    sell_reason TEXT,
    profit_rate REAL,
    holding_days INTEGER,

    -- AI 복기 결과
    situation_analysis TEXT,   -- JSON: 상황 분석
    judgment_evaluation TEXT,  -- JSON: 판단 평가
    lessons TEXT,              -- JSON: 교훈 목록
    pattern_tags TEXT,         -- JSON: 패턴 태그
    one_line_summary TEXT,     -- 한 줄 요약
    confidence_score REAL,     -- 분석 신뢰도 (0~1)

    -- 압축 관리
    compression_layer INTEGER DEFAULT 1,  -- 1: 상세, 2: 요약, 3: 직관
    compressed_summary TEXT,   -- Layer 2+ 압축 요약

    -- 메타데이터
    created_at TEXT NOT NULL,
    last_compressed_at TEXT
);

-- 인덱스
CREATE INDEX idx_journal_ticker ON trading_journal(ticker);
CREATE INDEX idx_journal_pattern ON trading_journal(pattern_tags);
CREATE INDEX idx_journal_date ON trading_journal(trade_date);
```

### trading_intuitions 테이블

```sql
CREATE TABLE trading_intuitions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- 분류
    category TEXT NOT NULL,    -- sector, market, pattern, rule
    subcategory TEXT,          -- 세부 분류

    -- 직관 내용
    condition TEXT NOT NULL,   -- 조건: "이런 상황에서..."
    insight TEXT NOT NULL,     -- 행동: "이렇게 해야 한다..."
    confidence REAL,           -- 신뢰도 (0~1)

    -- 증거
    supporting_trades INTEGER, -- 뒷받침하는 거래 수
    success_rate REAL,         -- 적중률
    source_journal_ids TEXT,   -- JSON: 소스 일지 ID들

    -- 관리
    created_at TEXT NOT NULL,
    last_validated_at TEXT,
    is_active INTEGER DEFAULT 1
);

CREATE INDEX idx_intuitions_category ON trading_intuitions(category);
```

## 활용 사례

### 1. 새 매수 결정 시 과거 경험 참조

```python
# stock_tracking_agent.py에서 자동으로 호출됨
context = agent._get_relevant_journal_context(
    ticker="005930",
    sector="반도체"
)

adjustment, reasons = agent._get_score_adjustment_from_context(
    ticker="005930",
    sector="반도체"
)

# 결과 예시:
# adjustment = -0.5  (과거 손실 이력으로 점수 하향)
# reasons = ["동일 종목 최근 3회 연속 손실 (-8%, -9%, -10%)"]
```

### 2. 압축 상태 확인

```python
stats = agent.get_compression_stats()
# {
#     "entries_by_layer": {
#         "layer1_detailed": 15,
#         "layer2_summarized": 45,
#         "layer3_compressed": 120
#     },
#     "active_intuitions": 28,
#     "oldest_uncompressed": "2024-01-15",
#     "avg_intuition_confidence": 0.72,
#     "avg_intuition_success_rate": 0.68
# }
```

### 3. 직관 조회

```python
# 반도체 섹터 관련 직관 조회
agent.cursor.execute("""
    SELECT condition, insight, confidence, success_rate
    FROM trading_intuitions
    WHERE subcategory = '반도체' AND is_active = 1
    ORDER BY confidence DESC
    LIMIT 5
""")
```

## 테스트

```bash
# 단위 테스트 실행
pytest tests/test_trading_journal.py -v

# 빠른 통합 테스트
python tests/test_trading_journal.py
```

## 향후 계획

1. **Intuition Validator Agent**: 직관을 최근 거래 결과와 대조하여 신뢰도 자동 조정
2. **Context Retriever Agent**: 매수 결정 시 관련 과거 경험을 능동적으로 검색
3. **Dashboard 통합**: 직관 목록과 압축 상태를 웹 대시보드에서 시각화
