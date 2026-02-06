# Jeon Ingu Contrarian Trading - 전인구경제연구소 역발상 투자 시뮬레이터

> **자동화된 유튜브 분석 및 역발상 투자 시뮬레이션 시스템**
>
> 전인구경제연구소 채널을 모니터링하고, AI로 분석하여, 반대 방향으로 베팅하는 컨트리언 전략을 시뮬레이션합니다.

---

## 📋 목차

1. [개요](#개요)
2. [주요 기능](#주요-기능)
3. [시스템 구조](#시스템-구조)
4. [설치 방법](#설치-방법)
5. [설정 가이드](#설정-가이드)
6. [사용 방법](#사용-방법)
7. [데이터베이스 구조](#데이터베이스-구조)
8. [웹 대시보드 연동](#웹-대시보드-연동)
9. [문제 해결](#문제-해결)

---

## 개요

### 전인구경제연구소란?

[전인구경제연구소](https://www.youtube.com/@전인구경제연구소)는 유튜브 채널로, 한국 주식시장과 경제 분석 콘텐츠를 제공합니다.

### 역발상 투자(Contrarian Investment)란?

**역발상 투자**는 시장 주류 의견과 반대 방향으로 베팅하는 전략입니다:

- **상승 예측 시** → 하락에 베팅 (인버스 ETF)
- **하락 예측 시** → 상승에 베팅 (레버리지 ETF)

### 시스템 목적

본 시스템은:
1. 전인구경제연구소의 신규 영상을 자동으로 모니터링
2. Whisper API로 음성을 텍스트로 변환
3. GPT-5로 시장 전망 분석
4. 역발상 투자 전략 생성 (JSON 형식)
5. 텔레그램 채널에 분석 요약 메시지 발송
6. **pykrx로 실시간 가격 조회**
7. **시뮬레이션 매매 실행** (전액 투자 전략)
8. **SQLite 이력 저장** (단일 통합 테이블)
9. 텔레그램 채널에 포트폴리오 상태 메시지 발송
10. 웹 대시보드를 통한 시각화

⚠️ **실제 매매 연동이 아닌 시뮬레이션**입니다. PRISM-INSIGHT의 실제 계좌와 구분되도록 별도 테이블(`jeoningu_trades`)로 관리합니다.

**핵심 전략**:
- **전액 투자 (All-in)**: 매수 시 가용 잔액 100% 투자
- **레버리지/인버스 2X**: 수익률 극대화를 위한 고위험-고수익 추구
- **역발상 베팅**: 전인구의 예측과 정반대 방향으로 투자

---

## 주요 기능

### 🤖 자동화 워크플로우

```
┌─────────────────────────────────────────────────────────────┐
│               Jeon Ingu Contrarian Trading                  │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. RSS Feed Monitoring                                     │
│     └─ Detect new videos from 전인구경제연구소              │
│                                                              │
│  2. Audio Extraction (yt-dlp + FFmpeg)                      │
│     └─ Download and extract audio as MP3                    │
│                                                              │
│  3. Transcription (OpenAI Whisper)                          │
│     ├─ Direct transcription (<25MB)                         │
│     └─ Chunked transcription (>25MB, 10-min chunks)         │
│                                                              │
│  4. AI Analysis (GPT-5)                                     │
│     ├─ Content type detection (본인의견 vs 스킵)           │
│     ├─ Market sentiment analysis (상승/하락/중립)          │
│     ├─ Contrarian strategy generation                       │
│     └─ Structured JSON output                               │
│                                                              │
│  5. Telegram Broadcasting (Analysis)                        │
│     └─ Send analysis summary to Telegram channel           │
│                                                              │
│  6. Simulated Trading Execution                             │
│     ├─ Calculate position size (100% all-in)                │
│     ├─ Execute BUY/SELL (simulated)                         │
│     └─ Save to SQLite database                              │
│                                                              │
│  7. Telegram Broadcasting (Portfolio Status)                │
│     ├─ Send current position info                           │
│     ├─ Send balance and performance metrics                 │
│     └─ Show cumulative return                               │
│                                                              │
│  8. Performance Tracking                                    │
│     ├─ Calculate win rate, cumulative return                │
│     └─ Export data for dashboard visualization              │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### 📊 데이터 관리

- **구조화된 JSON 출력**: Markdown 대신 정형화된 데이터 생성
- **SQLite 영속성**: 영상, 분석, 매매 이력 모두 DB에 저장
- **텔레그램 통합**: 요약 메시지 자동 발송
- **대시보드 연동**: `examples/dashboard`에서 시각화

---

## 시스템 구조

### 파일 구조

```
prism-insight/
├── stock_tracking_db.sqlite     # 통합 SQLite DB (PRISM-INSIGHT 메인 DB)
└── events/
    ├── jeoningu_trading.py          # 메인 스크립트
    ├── jeoningu_trading_db.py       # SQLite 데이터베이스 관리
    ├── jeoningu_price_fetcher.py    # 실시간 가격 조회 (pykrx)
    ├── JEONINGU_TRADING.md          # 본 문서
    ├── jeoningu_video_history.json  # 영상 이력 (자동 생성)
    │
    ├── logs/                         # 로그 파일 저장 디렉토리
    │   └── jeoningu_YYYYMMDD.log    # 일별 로그 파일
    │
    ├── transcripts/                  # 자막 파일 저장 디렉토리
    │   └── transcript_*.txt         # 영상별 자막 파일
    │
    └── audio_temp/                   # 임시 오디오 파일 디렉토리
        ├── temp_audio.mp3           # 임시 오디오 (자동 삭제)
        └── temp_audio_chunk_*.mp3   # 분할된 임시 파일 (자동 삭제)
```

**산출물 정리**:
- ✅ **로그 파일**: `logs/` 디렉토리에 날짜별로 저장
- ✅ **자막 파일**: `transcripts/` 디렉토리에 영상 ID별로 저장
- ✅ **임시 오디오**: `audio_temp/` 디렉토리에 저장 후 자동 삭제
- ✅ `.gitignore`에 하위 디렉토리 설정되어 있음 (버전 관리 제외)

### 데이터베이스 테이블

**주의**: PRISM-INSIGHT 메인 데이터베이스(`stock_tracking_db.sqlite`)에 통합되어 있습니다.

#### 단일 통합 테이블 설계

전인구 시뮬레이션은 **단 1개의 테이블**(`jeoningu_trades`)만 사용합니다:
- 각 영상당 1개의 row
- 영상 정보 + AI 분석 + 거래 정보가 모두 포함
- `related_buy_id`를 통해 매수-매도 연결

---

## 설치 방법

### 1. 사전 준비 사항

#### Python 3.10+
```bash
python --version  # 3.10 이상 확인
```

#### FFmpeg (오디오 처리용)
```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg

# Rocky Linux/CentOS
sudo dnf install ffmpeg
```

### 2. Python 패키지 설치

필요한 패키지가 이미 `requirements.txt`에 포함되어 있습니다:
```bash
pip install -r requirements.txt
```

주요 패키지:
- `openai`: Whisper API 및 GPT-5
- `yt-dlp`: YouTube 오디오 추출
- `feedparser`: RSS 파싱
- `pydub`: 오디오 파일 분할 (>25MB 파일용)
- `aiosqlite`: 비동기 SQLite
- `python-telegram-bot`: 텔레그램 연동
- `mcp-agent`: AI 에이전트 프레임워크
- `pykrx`: 한국 주식 실시간 가격 조회

### 3. 설정 파일 준비

프로젝트 루트에서:
```bash
# mcp_agent.secrets.yaml 설정
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
vi mcp_agent.secrets.yaml

# .env 설정 (텔레그램용)
cp .env.example .env
vi .env
```

---

## 설정 가이드

### 1. OpenAI API Key 설정

**파일**: `mcp_agent.secrets.yaml`

```yaml
openai:
  api_key: "sk-..." # 실제 OpenAI API 키 입력
```

- OpenAI API 키는 [OpenAI Platform](https://platform.openai.com/api-keys)에서 발급
- Whisper API 및 GPT-5 사용

### 2. Telegram 설정 (선택사항)

**파일**: `.env`

```bash
# Telegram Bot Token (BotFather에서 발급)
TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"

# Telegram Channel ID (메시지를 보낼 채널)
TELEGRAM_CHANNEL_ID="-1001234567890"
```

텔레그램 봇 생성 방법:
1. Telegram에서 [@BotFather](https://t.me/BotFather) 찾기
2. `/newbot` 명령으로 봇 생성
3. Bot Token 복사
4. 봇을 채널에 추가하고 관리자 권한 부여
5. Channel ID 확인 (봇으로 메시지 보낸 후 `getUpdates` API로 확인)

텔레그램 사용 안 할 경우:
```bash
python events/jeoningu_trading.py --no-telegram
```

### 3. MCP Agent 설정

**파일**: `mcp_agent.config.yaml`

`reasoning_effort` 값을 확인하세요:
```yaml
openai:
  default_model: gpt-5
  reasoning_effort: medium  # 'none'이 아닌 'low', 'medium', 'high' 중 하나
```

---

## 사용 방법

### 일반 모드 (RSS 모니터링)

신규 영상을 자동으로 감지하고 처리:

```bash
python events/jeoningu_trading.py
```

**첫 실행 시**:
- 기존 영상 이력을 초기화만 하고 종료
- 두 번째 실행부터 신규 영상 감지 및 처리

**두 번째 실행부터**:
- 신규 영상 감지
- 오디오 추출 → 자막 변환 → AI 분석 → 텔레그램 발송 → 시뮬레이션 매매

### 테스트 모드 (특정 영상 URL)

특정 영상만 분석:

```bash
python events/jeoningu_trading.py --video-url "https://www.youtube.com/watch?v=VIDEO_ID"
```

예시:
```bash
python events/jeoningu_trading.py --video-url "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
```

### 텔레그램 비활성화

텔레그램 메시지 발송 건너뛰기:

```bash
python events/jeoningu_trading.py --no-telegram
```

### Cron 자동화

매일 특정 시간에 자동 실행:

```bash
# Crontab 편집
crontab -e

# 매일 오후 6시에 실행
0 18 * * * cd /path/to/prism-insight && python events/jeoningu_trading.py >> events/cron.log 2>&1
```

---

## 데이터베이스 구조

### 통합 데이터베이스 설계

전인구 시뮬레이션은 PRISM-INSIGHT 메인 데이터베이스(`stock_tracking_db.sqlite`)에 **통합**되어 있습니다.

**장점**:
- 실제 거래 이력과 시뮬레이션 이력이 물리적으로 분리됨
- 하나의 DB 파일로 전체 시스템 관리
- 대시보드에서 통합 조회 가능

### 테이블 스키마

#### `jeoningu_trades` - 단일 통합 테이블

각 영상당 1개의 row가 생성되며, 영상 정보 + 분석 결과 + 거래 정보가 모두 포함됩니다.

| 컬럼 | 타입 | 필수 | 설명 |
|------|------|------|------|
| **기본 키** ||||
| `id` | INTEGER | ✓ | Auto-increment 거래 ID |
| **영상 정보** ||||
| `video_id` | TEXT | ✓ | YouTube 영상 ID (UNIQUE) |
| `video_title` | TEXT | ✓ | 영상 제목 |
| `video_date` | TEXT | ✓ | 영상 게시 날짜 (ISO 8601) |
| `video_url` | TEXT | ✓ | YouTube URL |
| `analyzed_date` | TEXT | ✓ | AI 분석 수행 날짜 |
| **AI 분석 결과** ||||
| `jeon_sentiment` | TEXT | ✓ | 전인구 기조 (상승/하락/중립) |
| `jeon_reasoning` | TEXT | | 전인구의 핵심 발언 요약 |
| `contrarian_action` | TEXT | ✓ | 역발상 액션 (인버스매수/KODEX매수/전량매도) |
| **거래 실행 정보** ||||
| `trade_type` | TEXT | | 거래 유형 (BUY/SELL/HOLD) |
| `stock_code` | TEXT | | 종목 코드 (069500 또는 114800) |
| `stock_name` | TEXT | | 종목명 (KODEX 200 또는 KODEX 인버스) |
| `quantity` | INTEGER | | 매수/매도 수량 |
| `price` | REAL | | 체결 가격 (pykrx에서 조회) |
| `amount` | REAL | | 거래 금액 (quantity × price) |
| **수익 추적** ||||
| `related_buy_id` | INTEGER | | 매도 시 연결된 매수 거래 ID (FK) |
| `profit_loss` | REAL | | 손익 금액 (매도 시) |
| `profit_loss_pct` | REAL | | 손익률 (%) |
| **포트폴리오 상태** ||||
| `balance_before` | REAL | ✓ | 거래 전 잔액 |
| `balance_after` | REAL | ✓ | 거래 후 잔액 |
| `cumulative_return_pct` | REAL | | 누적 수익률 (%) |
| **메타데이터** ||||
| `notes` | TEXT | | 추가 메모 |
| `created_at` | TEXT | | 레코드 생성 시각 (DEFAULT CURRENT_TIMESTAMP) |

**인덱스**:
- `idx_jeoningu_video_id` on `video_id`
- `idx_jeoningu_analyzed_date` on `analyzed_date DESC`
- `idx_jeoningu_trade_type` on `trade_type`

### 데이터 흐름 예시

#### 시나리오 1: 첫 매수
```sql
-- 전인구 "상승" → 역발상 인버스 매수
INSERT INTO jeoningu_trades (
  video_id, video_title, jeon_sentiment, contrarian_action,
  trade_type, stock_code, stock_name, quantity, price, amount,
  balance_before, balance_after
) VALUES (
  'abc123', '전인구: 시장 상승 예상', '상승', '인버스매수',
  'BUY', '114800', 'KODEX 인버스', 100, 10000, 1000000,
  10000000, 10000000  -- 현금→주식 전환이므로 balance 변동 없음
);
```

#### 시나리오 2: 중립 기조로 매도
```sql
-- 전인구 "중립" → 전량 매도
INSERT INTO jeoningu_trades (
  video_id, video_title, jeon_sentiment, contrarian_action,
  trade_type, stock_code, stock_name, quantity, price, amount,
  related_buy_id, profit_loss, profit_loss_pct,
  balance_before, balance_after, cumulative_return_pct
) VALUES (
  'def456', '전인구: 방향성 모호', '중립', '전량매도',
  'SELL', '114800', 'KODEX 인버스', 100, 10500, 1050000,
  1, 50000, 5.0,  -- 이전 매수(ID=1) 대비 +5%
  10000000, 10050000, 0.5  -- 잔액 증가, 누적 수익률 +0.5%
);
```

#### 시나리오 3: 보유 종목 없을 때 중립
```sql
-- 보유 종목 없는 상태에서 중립 → HOLD
INSERT INTO jeoningu_trades (
  video_id, video_title, jeon_sentiment, contrarian_action,
  trade_type, balance_before, balance_after, cumulative_return_pct, notes
) VALUES (
  'ghi789', '전인구: 여전히 중립', '중립', '관망',
  'HOLD', 10050000, 10050000, 0.5, '보유 종목 없음, 현금 보유'
);
```

### 주요 쿼리 패턴

#### 현재 보유 종목 확인
```python
# 로직: 마지막 BUY를 찾고, 그 이후 SELL이 있는지 확인
async def get_current_position():
    # 1. 마지막 BUY 찾기
    last_buy = SELECT * FROM jeoningu_trades 
               WHERE trade_type = 'BUY' 
               ORDER BY id DESC LIMIT 1
    
    # 2. 해당 BUY에 연결된 SELL이 있는지 확인
    sell_count = SELECT COUNT(*) FROM jeoningu_trades 
                 WHERE trade_type = 'SELL' 
                 AND related_buy_id = last_buy.id
    
    # 3. SELL이 없으면 현재 보유 중
    if sell_count == 0:
        return last_buy
    return None
```

#### 성과 지표 계산
```python
async def calculate_performance_metrics():
    # 모든 SELL 거래에서 손익 집계
    sell_trades = SELECT profit_loss, profit_loss_pct 
                  FROM jeoningu_trades 
                  WHERE trade_type = 'SELL'
    
    total_trades = len(sell_trades)
    winning_trades = count(profit_loss > 0)
    win_rate = winning_trades / total_trades * 100
    
    # 최신 누적 수익률
    latest = SELECT cumulative_return_pct, balance_after 
             FROM jeoningu_trades 
             ORDER BY id DESC LIMIT 1
    
    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "win_rate": win_rate,
        "cumulative_return": latest.cumulative_return_pct,
        "latest_balance": latest.balance_after
    }
```

### 데이터베이스 초기화

```bash
# Python으로 직접 초기화
python -c "import asyncio; from events.jeoningu_trading_db import init_database; asyncio.run(init_database())"
```

또는 `jeoningu_trading.py` 첫 실행 시 자동으로 초기화됩니다.

---

## 웹 대시보드 연동

### Dashboard 이벤트 탭 추가 (예정)

`examples/dashboard`의 Next.js 프론트엔드에 **이벤트 탭**을 추가하여 시각화:

#### 구현 예정 기능:
1. **거래 이력 테이블**: 매수/매도 이력을 표로 표시
2. **수익률 차트**: 누적 수익률 추이 그래프
3. **승률 분석**: 승/패 비율, 평균 수익률
4. **영상별 성과**: 어떤 영상이 가장 좋은 성과를 냈는지
5. **종목별 통계**: 어떤 종목이 자주 거래되었는지

#### API 엔드포인트 (백엔드 추가 필요):
```python
# examples/dashboard/backend/api/jeoningu.py

from events.jeoningu_trading_db import JeoninguTradingDB

@app.get("/api/jeoningu/trades")
async def get_trades(limit: int = 100):
    """Get recent trade history"""
    db = JeoninguTradingDB()
    await db.initialize()
    trades = await db.get_trade_history(limit=limit)
    return {"trades": trades}

@app.get("/api/jeoningu/position")
async def get_position():
    """Get current position"""
    db = JeoninguTradingDB()
    await db.initialize()
    position = await db.get_current_position()
    balance = await db.get_latest_balance()
    return {
        "position": position,
        "balance": balance
    }

@app.get("/api/jeoningu/performance")
async def get_performance():
    """Get performance metrics"""
    db = JeoninguTradingDB()
    await db.initialize()
    metrics = await db.calculate_performance_metrics()
    return metrics

@app.get("/api/jeoningu/dashboard")
async def get_dashboard():
    """Get all dashboard data"""
    db = JeoninguTradingDB()
    await db.initialize()
    data = await db.get_dashboard_data()
    return data
```

#### 프론트엔드 컴포넌트 (React):
```tsx
// components/JeoninguEventTab.tsx
import { useQuery } from 'react-query';

export function JeoninguEventTab() {
  const { data: dashboard } = useQuery('jeoningu-dashboard', fetchDashboard);

  return (
    <div>
      <h2>전인구 역발상 투자 시뮬레이션</h2>

      {/* 성과 요약 */}
      <MetricsSummary 
        metrics={dashboard?.performance} 
        balance={dashboard?.current_balance}
      />

      {/* 현재 포지션 */}
      <CurrentPosition position={dashboard?.current_position} />

      {/* 수익률 차트 */}
      <CumulativeReturnChart history={dashboard?.trade_history} />

      {/* 거래 이력 테이블 */}
      <TradesTable trades={dashboard?.trade_history} />
    </div>
  );
}
```

---

## 문제 해결

### 1. `pydub`가 설치되지 않음

**증상**:
```
ImportError: pydub is not installed
```

**해결**:
```bash
pip install pydub
```

FFmpeg도 함께 설치 필요:
```bash
# Ubuntu/Debian
sudo apt-get install ffmpeg

# macOS
brew install ffmpeg
```

### 2. Whisper API 25MB 제한 오류

**증상**:
```
Error: File size exceeds 25MB limit
```

**해결**:
- 자동으로 10분 청크로 분할하여 처리
- `_transcribe_large_file()` 메서드가 자동 처리
- FFmpeg가 설치되어 있어야 함

### 3. Telegram 메시지 발송 실패

**증상**:
```
Failed to send Telegram message
```

**확인 사항**:
1. `.env`에 `TELEGRAM_BOT_TOKEN`과 `TELEGRAM_CHANNEL_ID` 설정 확인
2. 봇이 채널에 관리자 권한으로 추가되어 있는지 확인
3. Channel ID가 `-100` 으로 시작하는지 확인 (슈퍼그룹의 경우)

**임시 회피**:
```bash
python events/jeoningu_trading.py --no-telegram
```

### 4. JSON 파싱 에러

**증상**:
```
Failed to parse JSON from LLM response
```

**원인**:
- GPT-5가 가끔 JSON을 마크다운 코드블록으로 감싸서 반환

**해결**:
- 스크립트에 자동 클리닝 로직 포함됨
- `result_clean = result.strip()` 등으로 처리

### 5. MCPApp context 에러

**증상**:
```
RuntimeError: No context available for OpenAIAugmentedLLM
```

**해결**:
`mcp_agent.config.yaml` 확인:
```yaml
openai:
  reasoning_effort: medium  # 'none'이 아닌 값으로 설정
```

### 6. 데이터베이스 초기화 안 됨

**증상**:
```
sqlite3.OperationalError: no such table: jeoningu_trades
```

**해결**:
첫 실행 시 데이터베이스가 자동으로 초기화되지만, 수동으로 초기화하려면:
```bash
python -c "import asyncio; from events.jeoningu_trading_db import init_database; asyncio.run(init_database())"
```

또는 테스트 스크립트 실행:
```bash
python events/jeoningu_trading_db.py
```

### 7. 영상이 감지되지 않음

**확인 사항**:
1. RSS URL이 올바른지 확인:
   ```python
   https://www.youtube.com/feeds/videos.xml?channel_id=UCznImSIaxZR7fdLCICLdgaQ
   ```
2. 첫 실행 후 두 번째 실행부터 신규 영상 감지됨
3. `jeoningu_video_history.json` 파일 확인

---

## 추가 정보

### 거래 종목

본 시스템은 **2개의 레버리지/인버스 ETF만** 사용합니다:

#### 1. KODEX 레버리지 (122630)
- **용도**: 상승 베팅 (2배 레버리지)
- **설명**: 코스피 200 지수를 2배로 추종하는 레버리지 ETF
- **전략**: 전인구 "하락" 예측 → 역발상으로 KODEX 레버리지 매수
- **특징**: 시장 상승 시 2배 수익, 하락 시 2배 손실

#### 2. KODEX 200선물인버스2X (252670)
- **용도**: 하락 베팅 (2배 인버스)
- **설명**: 코스피 200 지수의 반대 방향으로 2배 움직이는 인버스 ETF
- **전략**: 전인구 "상승" 예측 → 역발상으로 KODEX 200선물인버스2X 매수
- **특징**: 시장 하락 시 2배 수익, 상승 시 2배 손실

#### 포지션 관리 규칙
- 항상 **1개 종목만 보유**
- **전액 투자 전략**: 매수 시 가용 잔액 100% 투자 (All-in)
- 초기 자본: **1천만원**
- 종목 전환 시: 기존 종목 매도 → 새 종목 매수
- 중립 시: 보유 종목 있으면 전량 매도 (현금화)

#### 가격 조회
- **pykrx** 라이브러리로 실시간 가격 조회
- `jeoningu_price_fetcher.py` 모듈 사용
- 최근 거래일의 종가 기준

#### 왜 레버리지/인버스 2X인가?
- **수익률 극대화**: 일반 ETF 대비 2배의 변동성으로 단기 수익 극대화
- **명확한 방향성**: 전인구의 예측과 정반대로 베팅하므로, 2배 레버리지가 전략에 부합
- **리스크 인지**: 2배 손실 가능성도 있지만, 역발상 전략의 특성상 고위험-고수익 추구

### 참고 자료

- [전인구경제연구소 YouTube 채널](https://www.youtube.com/@전인구경제연구소)
- [OpenAI Whisper API 문서](https://platform.openai.com/docs/guides/speech-to-text)
- [yt-dlp GitHub](https://github.com/yt-dlp/yt-dlp)
- [python-telegram-bot 문서](https://docs.python-telegram-bot.org/)

### 라이선스

본 시스템은 PRISM-INSIGHT 프로젝트의 일부로, 동일한 라이선스를 따릅니다.

### 면책 조항

⚠️ **본 시스템은 교육 및 연구 목적으로 제작되었습니다.**

- 실제 투자 권유가 아닙니다
- 역발상 전략은 높은 리스크를 수반합니다
- 투자 결정은 본인의 판단과 책임 하에 이루어져야 합니다
- 시뮬레이션 결과가 실제 수익을 보장하지 않습니다

---

**Version**: 2.0
**Last Updated**: 2025-11-23
**Maintainer**: PRISM-INSIGHT Development Team
