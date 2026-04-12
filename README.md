# Standalone Prism-Insight Pub/Sub Trading Subscriber

Minimal PRISM-INSIGHT reduction focused on one runtime:

`GCP Pub/Sub -> signal validation -> KR/US KIS execution`

## Upstream basis

This repository is based on the original PRISM-INSIGHT project:

https://github.com/dragon1086/prism-insight

## What remains

- `subscriber.py` — production entrypoint
- `trading/` — KIS auth, KR trader, US trader, validation, routing, market-hours, demo off-hours queue
- `tests/` — focused regression coverage for the surviving runtime

## What was removed

- Analysis/orchestration/report generation
- Telegram, Firebase, Redis, dashboards, mobile integrations
- Trigger screening and publisher flows
- Non-trading docs/examples/tests

## Required configuration

### 1) Environment variables

Copy `.env.example` to `.env` and fill in:

```bash
GCP_PROJECT_ID=your-project-id
GCP_PUBSUB_SUBSCRIPTION_ID=your-subscription-id
GCP_CREDENTIALS_PATH=/absolute/path/to/service-account.json
```

### 2) KIS config

Copy `trading/config/kis_devlp.yaml.example` to `trading/config/kis_devlp.yaml` and provide your real KIS credentials/accounts.

## Install

```bash
pip install -r requirements.txt
```

## Run

Dry-run:

```bash
python subscriber.py --dry-run
```

Live mode:

```bash
python subscriber.py
```

## Signal contract

Supported inbound message fields:

- `type`: `BUY`, `SELL`, `EVENT`
- `ticker`
- `company_name`
- `market`: `KR` or `US`
- `price`
- `target_price`
- `stop_loss`
- `buy_score`
- `rationale`
- `profit_rate`
- `sell_reason`
- `buy_price`
- `event_type`, `source`, `event_description`

Behavior:

- `BUY` / `SELL`:
  - market open -> execute
  - market closed + demo mode -> queue until next open
  - market closed + real mode -> reject and ack
- `EVENT` -> log and ack, no trade
- malformed/unsupported payload -> log and ack

## Focused verification

```bash
pytest tests/test_signal_schema.py tests/test_dispatch.py tests/test_market_hours.py tests/test_off_hours_policy.py tests/test_subscriber_smoke.py tests/test_multi_account_domestic.py tests/test_multi_account_kis_auth.py tests/test_multi_account_us.py
```

## Docker

### One-click Linux installer

Linux 호스트에서는 아래 한 줄로 설치 스크립트를 내려받아 바로 실행할 수 있습니다:

```bash
curl -fsSL https://raw.githubusercontent.com/tkgo11/prism-insight-light/6e32cb0b7f8433378b1aec5969983221bd90bb2b/install_prism_docker.sh | bash
```

설치 스크립트가 자동으로 처리하는 항목:

- 고정된 ref 기준으로 프로젝트 다운로드
- `.env.example` → `.env` 생성 및 필수 Pub/Sub 값 입력
- `trading/config/kis_devlp.yaml.example` 기반 KIS 설정 준비
- Docker 이미지/컨테이너 정의 생성
- 선택 시 crontab 자동화 설치

사용자가 직접 확인해야 하는 항목:

- 실제 GCP 서비스 계정 JSON 경로
- 실제 KIS 계좌/앱키/시크릿
- 필요 시 시스템 타임존을 `Asia/Seoul`로 변경할지 여부
- crontab 자동 설치 여부

지원 범위:

- **Linux 호스트 전용**
- `bash`, `tar`, `curl` 또는 `wget`, `docker` 필요
- cron 자동화까지 사용할 경우 `crontab`, `timedatectl`/`sudo`가 필요할 수 있음

KIS 설정 방식은 두 가지를 지원합니다:

1. **guided** — 단일 기본 계좌와 공통 App Key/Secret 위주의 빠른 프롬프트
2. **manual** — `kis_devlp.yaml` 예제를 직접 수정하는 고급 경로 (다중 계좌 권장)

이미 설치한 디렉토리에서 `.env` 또는 `kis_devlp.yaml`을 수정했다면 같은 설치 스크립트를 다시 실행해 컨테이너 정의를 갱신할 수 있습니다.

### Manual Docker fallback

직접 저장소를 내려받아 수동으로 운용하려면:

```bash
docker build -t pubsub-trader .
docker run --rm --env-file .env -v /absolute/path/to/kis_devlp.yaml:/app/trading/config/kis_devlp.yaml pubsub-trader
```

장 시간에만 Docker 컨테이너를 자동 운용하려면:

```bash
bash setup_subscriber_docker_crontab.sh
```

`setup_subscriber_docker_crontab.sh`는 설치 시 컨테이너를 현재 설정으로 한 번 생성하고, 이후에는 시장 시간에 맞춰 `docker start` / `docker stop`만 수행합니다. `.env`를 바꿨다면 스크립트를 다시 실행해 컨테이너 정의를 재생성하세요.
