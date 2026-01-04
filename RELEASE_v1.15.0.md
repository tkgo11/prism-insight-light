## PRISM-INSIGHT v1.15.0
발표일: 2026년 1월 5일

### 주요 변경사항

#### KRX 데이터 수집 방식 변경 (Breaking Change)
KRX 거래소의 로그인 시스템 도입으로 기존 pykrx 라이브러리 사용이 제한되어, 자체 로그인 크롤링 방식(krx_data_client)으로 전환했습니다.

**필수 설정:**
```bash
# .env
KAKAO_ID=your_kakao_email@example.com
KAKAO_PW=your_kakao_password
```

```yaml
# mcp_agent.config.yaml
kospi_kosdaq:
  command: "python3"
  args: ["-m", "kospi_kosdaq_stock_server"]
  env:
    KAKAO_ID: "your_kakao_id"
    KAKAO_PW: "your_kakao_password"
```

#### 트리거 선정 알고리즘 개선
기존 각 트리거 카테고리별 단순 1위 선정 방식에서, 매수/매도 에이전트 로직과 궁합이 좋은 종목을 선정하는 하이브리드 방식으로 변경했습니다.

#### 매매일지(Trading Journal) 에이전트 신규 추가 (Optional)
- 매수/매도 에이전트와 연동되는 매매일지 시스템 추가
- JSON 기반 매매 기록 관리
- 장기기억 압축(Memory Compression) 알고리즘 적용
  - 최신 기록: 상세 보관
  - 오래된 기록: 요약 압축하여 장기기억으로 전환
- 과거 매매 패턴 학습을 통한 의사결정 품질 향상 기대

**기본값: 비활성화 (기존 사용자 영향 없음)**

활성화하려면 `.env`에 다음 설정 추가:
```bash
ENABLE_TRADING_JOURNAL=true
```

#### 연말 휴장일 체크 로직 추가
12월 31일 휴장일 자동 인식 기능 추가

#### kospi-kosdaq-stock-server 업그레이드
버전 0.3.8로 업그레이드

### 업데이트 방법
```bash
pip install -r requirements.txt
python3 -m playwright install chromium
```
