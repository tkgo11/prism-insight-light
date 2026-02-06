# PRISM-INSIGHT 실시간 트레이딩 시그널 구독 가이드

PRISM-INSIGHT의 AI 기반 실시간 매매 시그널을 GCP Pub/Sub을 통해 받아볼 수 있습니다.

## 📋 개요

- **무료 제공**: PRISM-INSIGHT 측 비용 없음
- **실시간 스트림**: 매수/매도 시그널을 즉시 수신
- **커스터마이징 가능**: 받은 시그널로 자체 로직 구현 가능
- **샘플 코드 제공**: Python 예제 코드 포함

## 💰 비용 안내

### PRISM-INSIGHT 측
- 무료 (Topic 운영 비용은 PRISM-INSIGHT가 부담)

### 구독자 측 (본인 GCP 프로젝트)
- **GCP Pub/Sub 요금**: https://cloud.google.com/pubsub/pricing
- **무료 할당량**: 월 10GB까지 무료
- **예상 비용**: 시그널이 적어 대부분 무료 범위 내

## 🚀 빠른 시작

### 1. GCP 계정 및 프로젝트 생성

1. GCP 계정이 없다면: https://console.cloud.google.com (무료 계정 가능)
2. 새 프로젝트 생성:
   - 프로젝트 이름: 원하는 이름 (예: `my-prism-subscriber`)
   - 프로젝트 ID 기록: `my-prism-subscriber-12345`

### 2. Pub/Sub API 활성화

```bash
# gcloud CLI 설치되어 있다면
gcloud services enable pubsub.googleapis.com --project=MY_PROJECT_ID

# 또는 웹 콘솔에서
# GCP Console → API 및 서비스 → 라이브러리 → "Cloud Pub/Sub API" 검색 → 사용
```

### 3. 구독(Subscription) 생성

#### 방법 A: gcloud CLI 사용 (권장)

```bash
# 프로젝트 설정
gcloud config set project MY_PROJECT_ID

# 구독 생성
gcloud pubsub subscriptions create my-prism-signals \
  --topic=projects/galvanized-sled-435607-p6/topics/prism-trading-signals \
  --project=MY_PROJECT_ID

# 구독 확인
gcloud pubsub subscriptions list
```

#### 방법 B: GCP 웹 콘솔 사용

1. https://console.cloud.google.com/cloudpubsub/subscription/list
2. "구독 만들기" 클릭
3. 구독 ID: `my-prism-signals` (원하는 이름)
4. "Cloud Pub/Sub 주제 선택" 클릭
5. "다른 프로젝트의 주제 입력" 선택
6. 입력: `projects/galvanized-sled-435607-p6/topics/prism-trading-signals`

   **개발 중 테스트를 위한 토픽도 따로 있습니다. 처음엔 이 토픽 사용 권장드립니다 (prism-trading-signals-test)**

7. 전송 유형: Pull
8. "만들기" 클릭

### 4. 서비스 계정 생성 및 키 다운로드

1. https://console.cloud.google.com/iam-admin/serviceaccounts
2. "서비스 계정 만들기" 클릭
3. 이름: `prism-subscriber`
4. 역할: "Pub/Sub 구독자" 선택
5. 완료 후 서비스 계정 클릭
6. "키" 탭 → "키 추가" → "새 키 만들기"
7. JSON 선택 → 생성
8. 다운로드된 JSON 파일 안전하게 보관

### 5. 예제 코드 실행

#### Python 환경 설정

```bash
# 저장소 클론
git clone https://github.com/your-repo/prism-insight.git
cd prism-insight

# 가상환경 생성 (권장)
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 패키지 설치
pip install google-cloud-pubsub python-dotenv
```

#### 환경 변수 설정

`.env` 파일 생성:
```bash
GCP_PROJECT_ID=MY_PROJECT_ID
GCP_PUBSUB_SUBSCRIPTION_ID=my-prism-signals
GCP_CREDENTIALS_PATH=/path/to/downloaded-key.json
```

#### 구독자 실행

```bash
# 테스트 모드 (실제 매매 X)
python examples/messaging/gcp_pubsub_subscriber_example.py --dry-run

# 실제 매매 모드 (주의!)
python examples/messaging/gcp_pubsub_subscriber_example.py
```

## 📊 수신되는 데이터 형식

### 매수 시그널 (BUY)

```json
{
  "type": "BUY",
  "ticker": "005930",
  "company_name": "삼성전자",
  "price": 82000,
  "timestamp": "2025-01-15T10:30:00",
  "target_price": 90000,
  "stop_loss": 75000,
  "investment_period": "단기",
  "sector": "반도체",
  "rationale": "AI 반도체 수요 증가",
  "buy_score": 8,
  "source": "AI분석",
  "trade_success": true,
  "trade_message": "매수 완료"
}
```

### 매도 시그널 (SELL)

```json
{
  "type": "SELL",
  "ticker": "005930",
  "company_name": "삼성전자",
  "price": 90000,
  "timestamp": "2025-01-20T14:20:00",
  "buy_price": 82000,
  "profit_rate": 9.76,
  "sell_reason": "목표가 달성",
  "source": "AI분석",
  "trade_success": true,
  "trade_message": "매도 완료"
}
```

### 이벤트 시그널 (EVENT)

```json
{
  "type": "EVENT",
  "ticker": "005930",
  "company_name": "삼성전자",
  "price": 82000,
  "timestamp": "2025-01-15T12:00:00",
  "event_type": "YOUTUBE",
  "event_description": "신규 영상 업로드",
  "source": "유튜버_홍길동"
}
```

## 💡 활용 예시

### 1. 커스텀 알림 시스템

```python
def callback(message):
    signal = json.loads(message.data.decode("utf-8"))
    
    if signal["type"] == "BUY" and signal["buy_score"] >= 8:
        # Slack, Discord, Email 등으로 알림
        send_notification(f"강력 매수: {signal['company_name']}")
    
    message.ack()
```

### 2. 자동매매 봇

```python
def callback(message):
    signal = json.loads(message.data.decode("utf-8"))
    
    if signal["type"] == "BUY":
        # 본인의 증권 API로 매수
        my_broker_api.buy(
            ticker=signal["ticker"],
            price=signal["price"]
        )
    
    message.ack()
```

### 3. 데이터 수집 및 분석

```python
def callback(message):
    signal = json.loads(message.data.decode("utf-8"))
    
    # 데이터베이스에 저장
    save_to_database(signal)
    
    # 백테스팅 데이터로 활용
    analyze_signal_performance(signal)
    
    message.ack()
```

### 4. 필터링 및 재가공

```python
def callback(message):
    signal = json.loads(message.data.decode("utf-8"))
    
    # 특정 섹터만 필터링
    if signal.get("sector") == "반도체":
        # 자체 Pub/Sub Topic으로 재발행
        my_publisher.publish(MY_TOPIC, json.dumps(signal))
    
    message.ack()
```

## 🔧 고급 설정

### 메시지 필터링 (서버 측)

특정 조건의 메시지만 받기:

```bash
gcloud pubsub subscriptions create my-filtered-signals \
  --topic=projects/PRISM_PROJECT_ID/topics/prism-trading-signals \
  --filter='attributes.signal_type="BUY"'
```

### 재시도 정책 설정

```bash
gcloud pubsub subscriptions update my-prism-signals \
  --min-retry-delay=10s \
  --max-retry-delay=600s
```

### Dead Letter Queue 설정

처리 실패한 메시지 별도 관리:

```bash
# Dead letter topic 생성
gcloud pubsub topics create my-prism-dlq

# 구독에 DLQ 설정
gcloud pubsub subscriptions update my-prism-signals \
  --dead-letter-topic=my-prism-dlq \
  --max-delivery-attempts=5
```

## 🛠️ 문제 해결

### 메시지가 수신되지 않음

1. **구독 확인**:
```bash
gcloud pubsub subscriptions describe my-prism-signals
```

2. **권한 확인**:
```bash
gcloud pubsub subscriptions get-iam-policy my-prism-signals
```

3. **Topic 주소 확인**: `projects/PRISM_PROJECT_ID/topics/prism-trading-signals`가 정확한지 확인

### 인증 오류

```bash
# 서비스 계정 키 경로 확인
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json

# 또는 .env 파일에서
GCP_CREDENTIALS_PATH=/path/to/key.json
```

### 비용 초과 우려

1. **할당량 설정**: GCP Console → Pub/Sub → 할당량에서 제한 설정
2. **구독 일시 중지**:
```bash
gcloud pubsub subscriptions update my-prism-signals \
  --no-enable-message-ordering
```

## 📞 지원 및 문의

- **GitHub Issues**: https://github.com/your-repo/prism-insight/issues
- **Telegram 채널**: @your_channel
- **문서**: https://github.com/your-repo/prism-insight/docs

## ⚠️ 면책 조항

- 본 시그널은 AI 기반 분석 결과이며 투자 권유가 아닙니다.
- 모든 투자 결정과 손실에 대한 책임은 전적으로 투자자 본인에게 있습니다.
- 실제 매매 전 충분한 검토와 테스트를 권장합니다.
- PRISM-INSIGHT는 시그널 정확성을 보장하지 않습니다.

## 🔄 업데이트 내역

- 2025-01-15: 초기 버전 공개
- Topic 공개: projects/PRISM_PROJECT_ID/topics/prism-trading-signals

---

**Happy Trading! 📈**
