# GCP Pub/Sub 웹 콘솔 설정 가이드

PRISM-INSIGHT에서 GCP Pub/Sub을 사용하기 위한 웹 콘솔 설정 방법입니다.

## 1. GCP 프로젝트 생성 또는 선택

1. https://console.cloud.google.com 접속
2. 상단의 프로젝트 선택 드롭다운 클릭
3. 기존 프로젝트 선택 또는 "새 프로젝트" 클릭
4. 프로젝트 이름 입력 (예: `prism-insight`)
5. "만들기" 클릭
6. 생성된 프로젝트 ID 기록 (예: `prism-insight-12345`)

## 2. Pub/Sub API 활성화

1. 좌측 메뉴 > "API 및 서비스" > "라이브러리" 클릭
2. 검색창에 "Pub/Sub" 입력
3. "Cloud Pub/Sub API" 클릭
4. "사용" 버튼 클릭

## 3. Topic 생성

1. 좌측 메뉴 > "Pub/Sub" > "주제" 클릭
2. "주제 만들기" 버튼 클릭
3. 주제 ID 입력: `prism-trading-signals`
4. 기본 설정 유지 (암호화, 스키마 등)
5. "만들기" 클릭

## 4. Subscription 생성

1. 좌측 메뉴 > "Pub/Sub" > "구독" 클릭
2. "구독 만들기" 버튼 클릭
3. 구독 ID 입력: `prism-trading-signals-sub`
4. Cloud Pub/Sub 주제 선택: `prism-trading-signals`
5. 전송 유형: "Pull" 선택 (기본값)
6. 승인 기한: 60초 (기본값)
7. 메시지 보존 기간: 7일 (기본값)
8. "만들기" 클릭

## 5. 서비스 계정 생성

1. 좌측 메뉴 > "IAM 및 관리자" > "서비스 계정" 클릭
2. "서비스 계정 만들기" 버튼 클릭
3. 서비스 계정 이름 입력: `prism-pubsub-service`
4. 서비스 계정 설명 (선택): "PRISM-INSIGHT Pub/Sub access"
5. "만들기 및 계속하기" 클릭
6. 역할 추가:
   - "역할 선택" 드롭다운 클릭
   - "Pub/Sub 게시자" 선택
   - "+ 다른 역할 추가" 클릭
   - "Pub/Sub 구독자" 선택
7. "계속" 클릭
8. "완료" 클릭

## 6. 서비스 계정 키 생성

1. 방금 생성한 서비스 계정 클릭
2. "키" 탭 클릭
3. "키 추가" > "새 키 만들기" 클릭
4. 키 유형: JSON 선택
5. "만들기" 클릭
6. 자동으로 다운로드되는 JSON 파일 저장
   - 예: `prism-insight-12345-abcdef123456.json`
7. **이 파일을 안전한 곳에 보관** (절대 Git에 커밋하지 말 것!)

## 7. .env 파일 설정

프로젝트 루트의 `.env` 파일에 다음 내용 추가:

```bash
# GCP Pub/Sub Configuration
GCP_PROJECT_ID=prism-insight-12345
GCP_PUBSUB_TOPIC_ID=prism-trading-signals
GCP_PUBSUB_SUBSCRIPTION_ID=prism-trading-signals-sub
GCP_CREDENTIALS_PATH=/path/to/prism-insight-12345-abcdef123456.json
```

**주의**: `GCP_CREDENTIALS_PATH`는 다운로드한 JSON 키 파일의 **절대 경로**를 입력해야 합니다.

## 8. 패키지 설치

```bash
pip install google-cloud-pubsub
```

## 9. 테스트

### Publisher 테스트

```python
import asyncio
from messaging.gcp_pubsub_signal_publisher import SignalPublisher

async def test_publish():
    async with SignalPublisher() as publisher:
        result = await publisher.publish_buy_signal(
            ticker="005930",
            company_name="삼성전자",
            price=82000,
            scenario={"target_price": 90000}
        )
        print(f"Published: {result}")

asyncio.run(test_publish())
```

### Subscriber 테스트

```bash
python examples/messaging/gcp_pubsub_subscriber_example.py --dry-run
```

## 요약

생성한 리소스:
- ✅ 프로젝트: `prism-insight-12345`
- ✅ Topic: `prism-trading-signals`
- ✅ Subscription: `prism-trading-signals-sub`
- ✅ 서비스 계정: `prism-pubsub-service`
- ✅ JSON 키 파일: `prism-insight-12345-abcdef123456.json`

.env 설정:
```
GCP_PROJECT_ID=프로젝트ID
GCP_PUBSUB_TOPIC_ID=prism-trading-signals
GCP_PUBSUB_SUBSCRIPTION_ID=prism-trading-signals-sub
GCP_CREDENTIALS_PATH=JSON파일경로
```

완료!
