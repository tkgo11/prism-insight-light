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

```bash
docker build -t pubsub-trader .
docker run --rm --env-file .env -v /absolute/path/to/kis_devlp.yaml:/app/trading/config/kis_devlp.yaml pubsub-trader
```

장 시간에만 Docker 컨테이너를 자동 운용하려면:

```bash
bash setup_subscriber_docker_crontab.sh
```

이 스크립트는 설치 시 컨테이너를 현재 설정으로 한 번 생성하고, 이후에는 시장 시간에 맞춰 `docker start` / `docker stop`만 수행합니다. `.env`를 바꿨다면 스크립트를 다시 실행해 컨테이너 정의를 재생성하세요.
