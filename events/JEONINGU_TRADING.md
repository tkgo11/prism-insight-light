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
5. 텔레그램 채널에 요약 메시지 발송
6. **시뮬레이션 매매 실행 및 SQLite 이력 저장**
7. 웹 대시보드를 통한 시각화

⚠️ **실제 매매 연동이 아닌 시뮬레이션**입니다. PRISM-INSIGHT의 실제 계좌와 이력이 섞이지 않도록 별도 DB로 관리합니다.

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
│  5. Telegram Broadcasting                                   │
│     └─ Send summary to Telegram channel                     │
│                                                              │
│  6. Simulated Trading Execution                             │
│     ├─ Calculate position size                              │
│     ├─ Execute BUY/SELL (simulated)                         │
│     └─ Save to SQLite database                              │
│                                                              │
│  7. Performance Tracking                                    │
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
events/
├── jeoningu_trading.py          # 메인 스크립트
├── jeoningu_trading_db.py       # SQLite 데이터베이스 관리
├── JEONINGU_TRADING.md          # 본 문서
├── jeoningu_trading.db          # SQLite 데이터베이스 (자동 생성)
├── jeoningu_video_history.json  # 영상 이력 (자동 생성)
├── jeoningu_YYYYMMDD.log        # 로그 파일 (자동 생성)
├── transcript_*.txt             # 자막 파일 (디버깅용)
└── temp_audio.*                 # 임시 오디오 파일 (자동 삭제)
```

### 데이터베이스 테이블

- **`videos`**: 분석한 영상 정보
- **`analysis_results`**: AI 분석 결과
- **`trades`**: 매수/매도 거래 이력
- **`portfolio`**: 현재 보유 종목
- **`performance_metrics`**: 성과 지표
- **`telegram_messages`**: 발송한 텔레그램 메시지

자세한 스키마는 [데이터베이스 구조](#데이터베이스-구조) 섹션 참조.

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

### ERD (Entity Relationship Diagram)

```
┌───────────────┐
│    videos     │
├───────────────┤
│ video_id (PK) │
│ title         │
│ published_date│
│ analyzed_date │
│ video_url     │
│ ...           │
└───────────────┘
        │
        │ 1:N
        ▼
┌───────────────────┐
│ analysis_results  │
├───────────────────┤
│ id (PK)           │
│ video_id (FK)     │
│ jeon_prediction   │
│ contrarian_strategy│
│ target_stocks     │
│ confidence_score  │
│ ...               │
└───────────────────┘
        │
        │ 1:N
        ▼
┌───────────────────┐       ┌───────────────┐
│     trades        │──────▶│   portfolio   │
├───────────────────┤  1:1  ├───────────────┤
│ id (PK)           │       │ stock_code(PK)│
│ video_id (FK)     │       │ stock_name    │
│ analysis_id (FK)  │       │ buy_trade_id  │
│ stock_code        │       │ quantity      │
│ trade_type (BUY/  │       │ avg_buy_price │
│            SELL)  │       │ ...           │
│ quantity          │       └───────────────┘
│ price             │
│ profit_loss       │
│ cumulative_return │
│ ...               │
└───────────────────┘
```

### 테이블 상세

#### `videos` - 영상 정보
| 컬럼 | 타입 | 설명 |
|------|------|------|
| `video_id` | TEXT (PK) | YouTube 영상 ID |
| `title` | TEXT | 영상 제목 |
| `published_date` | TEXT | 게시 날짜 (ISO 8601) |
| `analyzed_date` | TEXT | 분석 날짜 (ISO 8601) |
| `video_url` | TEXT | YouTube URL |
| `transcript_summary` | TEXT | 자막 요약 |

#### `analysis_results` - AI 분석 결과
| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | INTEGER (PK) | 분석 ID (자동 증가) |
| `video_id` | TEXT (FK) | 영상 ID |
| `jeon_prediction` | TEXT | 전인구 예측 (상승/하락/중립) |
| `jeon_reasoning` | TEXT | 예측 근거 |
| `contrarian_strategy` | TEXT | 역발상 전략 (매수/매도/관망) |
| `contrarian_reasoning` | TEXT | 전략 근거 |
| `target_stocks` | TEXT (JSON) | 추천 종목 리스트 |
| `sentiment_score` | REAL | 감정 점수 |
| `confidence_score` | REAL | 신뢰도 (0.0~1.0) |
| `raw_analysis_json` | TEXT (JSON) | 전체 분석 결과 JSON |

#### `trades` - 매매 이력
| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | INTEGER (PK) | 거래 ID (자동 증가) |
| `video_id` | TEXT (FK) | 관련 영상 ID |
| `analysis_id` | INTEGER (FK) | 분석 ID |
| `stock_code` | TEXT | 종목 코드 (6자리) |
| `stock_name` | TEXT | 종목명 |
| `trade_type` | TEXT | BUY 또는 SELL |
| `trade_date` | TEXT | 거래 날짜 (ISO 8601) |
| `quantity` | INTEGER | 수량 |
| `price` | REAL | 가격 |
| `total_amount` | REAL | 총 금액 |
| `related_buy_id` | INTEGER | 관련 매수 거래 ID (매도 시) |
| `profit_loss` | REAL | 손익 금액 |
| `profit_loss_rate` | REAL | 수익률 (%) |
| `cumulative_return` | REAL | 누적 수익률 (%) |
| `strategy_note` | TEXT | 전략 메모 |

#### `portfolio` - 현재 보유 종목
| 컬럼 | 타입 | 설명 |
|------|------|------|
| `stock_code` | TEXT (PK) | 종목 코드 |
| `stock_name` | TEXT | 종목명 |
| `buy_trade_id` | INTEGER (FK) | 매수 거래 ID |
| `video_id` | TEXT (FK) | 관련 영상 ID |
| `quantity` | INTEGER | 보유 수량 |
| `avg_buy_price` | REAL | 평균 매수가 |
| `total_investment` | REAL | 총 투자 금액 |
| `buy_date` | TEXT | 매수 날짜 |
| `strategy_note` | TEXT | 전략 메모 |

#### `performance_metrics` - 성과 지표
| 컬럼 | 타입 | 설명 |
|------|------|------|
| `id` | INTEGER (PK) | 메트릭 ID |
| `calculation_date` | TEXT | 계산 날짜 |
| `total_trades` | INTEGER | 전체 거래 수 |
| `winning_trades` | INTEGER | 수익 거래 수 |
| `losing_trades` | INTEGER | 손실 거래 수 |
| `win_rate` | REAL | 승률 (%) |
| `cumulative_return` | REAL | 누적 수익률 (%) |
| `avg_return_per_trade` | REAL | 평균 거래당 수익률 (%) |
| `max_drawdown` | REAL | 최대 낙폭 (MDD) |
| `sharpe_ratio` | REAL | 샤프 비율 |

### 데이터베이스 쿼리 예시

#### 전체 거래 이력 조회
```python
from events.jeoningu_trading_db import JeoninguTradingDB

db = JeoninguTradingDB()
await db.initialize()

# 최근 100개 거래
trades = await db.get_trade_history(limit=100)
for trade in trades:
    print(f"{trade['trade_date']}: {trade['trade_type']} {trade['stock_name']} x {trade['quantity']}")
```

#### 현재 포트폴리오 조회
```python
portfolio = await db.get_portfolio()
for position in portfolio:
    print(f"{position['stock_name']}: {position['quantity']}주 @ {position['avg_buy_price']}원")
```

#### 성과 지표 계산
```python
metrics = await db.calculate_performance_metrics()
print(f"승률: {metrics['win_rate']:.1f}%")
print(f"누적 수익률: {metrics['cumulative_return']:.2f}%")
```

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

@app.get("/api/jeoningu/trades")
async def get_trades(limit: int = 100):
    """Get recent trade history"""
    db = JeoninguTradingDB()
    trades = await db.get_trade_history(limit=limit)
    return {"trades": trades}

@app.get("/api/jeoningu/portfolio")
async def get_portfolio():
    """Get current portfolio"""
    db = JeoninguTradingDB()
    portfolio = await db.get_portfolio()
    return {"portfolio": portfolio}

@app.get("/api/jeoningu/performance")
async def get_performance():
    """Get performance metrics"""
    db = JeoninguTradingDB()
    metrics = await db.calculate_performance_metrics()
    return metrics
```

#### 프론트엔드 컴포넌트 (React):
```tsx
// components/JeoninguEventTab.tsx
import { useQuery } from 'react-query';

export function JeoninguEventTab() {
  const { data: trades } = useQuery('jeoningu-trades', fetchTrades);
  const { data: metrics } = useQuery('jeoningu-performance', fetchPerformance);

  return (
    <div>
      <h2>전인구 역발상 투자 시뮬레이션</h2>

      {/* 성과 요약 */}
      <MetricsSummary metrics={metrics} />

      {/* 수익률 차트 */}
      <CumulativeReturnChart trades={trades} />

      {/* 거래 이력 테이블 */}
      <TradesTable trades={trades} />
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
sqlite3.OperationalError: no such table: videos
```

**해결**:
첫 실행 시 데이터베이스가 자동으로 초기화되지만, 수동으로 초기화하려면:
```bash
python -c "import asyncio; from events.jeoningu_trading_db import init_database; asyncio.run(init_database())"
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

### 추천 종목 코드

#### 인버스 ETF (하락 베팅)
- `114800`: KODEX 인버스
- `252670`: TIGER 인버스
- `251340`: KODEX 코스닥150 인버스

#### 레버리지 ETF (상승 베팅)
- `122630`: KODEX 레버리지
- `233740`: TIGER 레버리지
- `233160`: KODEX 코스닥150 레버리지

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
