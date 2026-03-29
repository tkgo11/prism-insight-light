# PRISM INSIGHT
> This repository is based on [https://github.com/dragon1086/prism-insight](https://github.com/dragon1086/prism-insight)
> 
> Automated stock trading pipeline — **GCP Pub/Sub → KIS API execution** for Korean & US markets.

## Architecture

```
Signal Source (Pub/Sub) → subscriber.py → KIS API (Buy/Sell)
                              │
                              ├─ SQLite (scheduled orders, trade logs)
                              ├─ Notifications (Slack / Discord)
                              └─ Dashboard (FastAPI, :8000)
```

## Project Structure

```
prism-insight-light/
├── subscriber.py                # Main entry — Pub/Sub consumer & trade executor
├── dashboard.py                 # FastAPI web dashboard (status, orders, logs)
├── scripts/
│   └── setup_subscriber_cron.sh # Cron installer for Python/Docker subscriber runs
├── trading/
│   ├── kis_auth.py              # KIS API auth, async HTTP (aiohttp)
│   ├── base_trading.py          # Abstract base class for traders
│   ├── domestic_stock_trading.py  # Korean stock trading
│   ├── us_stock_trading.py      # US stock trading (NYSE/NASDAQ)
│   ├── database.py              # SQLAlchemy models (SQLite)
│   ├── notifier.py              # Slack & Discord webhook notifier
│   ├── analysis.py              # MarketDataBuffer (circular buffer)
│   ├── rate_limiter.py          # Async token-bucket rate limiter
│   ├── schemas.py               # Pydantic signal validation
│   ├── models.py                # Dataclasses (OrderResult, StockPrice, etc.)
│   ├── constants.py             # Shared config & market hours
│   └── kis_devlp.yaml.example   # KIS credentials template
├── static/
│   └── index.html               # Dashboard UI
├── tests/
│   ├── mock_kis_api.py          # Mock KIS API for offline testing
│   ├── test_db.py               # Database CRUD tests
│   ├── test_notifier.py         # Notifier tests (mocked webhooks)
│   ├── test_analysis.py         # MarketDataBuffer tests
│   └── test_integration.py      # End-to-end trading flow tests
├── docs/
│   ├── API.md                   # Module & endpoint reference
│   └── RUNBOOK.md               # Deployment & ops guide
├── requirements.txt
├── .env.example
└── README.md
```

## Quick Start

### 1. Install

```bash
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env           # GCP project, subscription, webhook URLs
cp trading/kis_devlp.yaml.example trading/kis_devlp.yaml  # KIS credentials
```

| Variable | Description |
|---|---|
| `GCP_PROJECT_ID` | Google Cloud project ID |
| `GCP_PUBSUB_SUBSCRIPTION_ID` | Pub/Sub subscription name |
| `GCP_CREDENTIALS_PATH` | Path to service account JSON |
| `SLACK_WEBHOOK_URL` | *(optional)* Slack incoming webhook |
| `DISCORD_WEBHOOK_URL` | *(optional)* Discord webhook |

KIS API credentials: [KIS Developers Portal](https://apiportal.koreainvestment.com/)

### 3. Run

```bash
# Dry-run (log signals, no real trades)
python subscriber.py --dry-run

# Live trading
python subscriber.py
```

### 4. Run with Docker

```bash
# Start subscriber + dashboard
docker compose up -d

# Start subscriber only
docker compose up -d subscriber

# Pass subscriber flags such as --dry-run
SUBSCRIBER_ARGS="--dry-run" docker compose up -d subscriber
```

### 5. Schedule with Cron

Use the helper script on a Linux host if you want cron to start and stop the subscriber automatically. Running `--install` in a terminal now opens an interactive setup wizard by default.

```bash
# Interactive install for a local Python process
bash scripts/setup_subscriber_cron.sh --install

# Interactive install for Docker Compose
EXECUTION_MODE=docker-compose bash scripts/setup_subscriber_cron.sh --install

# Interactive install for an existing container
EXECUTION_MODE=docker bash scripts/setup_subscriber_cron.sh --install

# Skip prompts and use env/default values
bash scripts/setup_subscriber_cron.sh --install --non-interactive
```

Default schedule is KST:

- KR `09:30-10:00` Monday-Friday
- KR `15:40-16:10` Monday-Friday
- US `02:30-02:50` Tuesday-Saturday
- US `06:30-06:50` Tuesday-Saturday

Useful overrides:

```bash
# Dry-run schedule
SUBSCRIBER_ARGS="--dry-run" bash scripts/setup_subscriber_cron.sh --install

# Leave subscriber running after each window
AUTO_SHUTDOWN=false bash scripts/setup_subscriber_cron.sh --install --non-interactive

# Custom KR morning window
KR_MORNING_START_CRON="35 9 * * 1-5" KR_MORNING_STOP_CRON="5 10 * * 1-5" bash scripts/setup_subscriber_cron.sh --install --non-interactive

# Inspect or remove the managed cron block
bash scripts/setup_subscriber_cron.sh --show
bash scripts/setup_subscriber_cron.sh --uninstall
```

### 6. Dashboard

```bash
python dashboard.py
# Open http://localhost:8000
```

## Features

| Feature | Description |
|---|---|
| **Dual Market** | Korean (KRX) and US (NYSE/NASDAQ) stock trading |
| **Async I/O** | Non-blocking API calls via `aiohttp` |
| **Rate Limiting** | Token-bucket limiter per market (KR: 20/s, US: 10/s) |
| **Signal Validation** | Pydantic schemas for BUY/SELL/EVENT signals |
| **Persistence** | SQLite for scheduled orders & trade logs |
| **Scheduled Orders** | Off-hours signals queued and executed at next market open |
| **Dashboard** | Real-time web UI — system status, orders, trade history |
| **Notifications** | Slack & Discord alerts on trade execution |
| **Market Analysis** | Circular buffer tracking price changes & moving averages |
| **Mock API** | Offline testing without real KIS credentials |

## Signal Format

Pub/Sub messages are expected as JSON:

```json
{
  "signal_type": "BUY",
  "ticker": "005930",
  "company_name": "삼성전자",
  "price": "71000",
  "market": "KR"
}
```

- `signal_type`: `BUY` | `SELL` | `EVENT`
- `market`: `KR` (default) | `US`

## Testing

```bash
python -m pytest tests/
```

## Docs

- [API Reference](docs/API.md)
- [Runbook](docs/RUNBOOK.md)
