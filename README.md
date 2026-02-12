# PRISM-INSIGHT-LIGHT

Automated stock trading pipeline: **GCP Pub/Sub subscriber → KIS API execution**.

## What It Does

Subscribes to a GCP Pub/Sub topic for trading signals (BUY/SELL) and automatically executes trades via the Korea Investment & Securities (KIS) API.

## Project Structure

```
prism-insight/
├── trading/
│   ├── __init__.py
│   ├── kis_auth.py                  # KIS API auth & HTTP helpers
│   ├── domestic_stock_trading.py    # Buy/sell Korean stocks
│   └── kis_devlp.yaml.example      # KIS config template
├── subscriber.py                    # Subscribe & execute trades
├── .env.example
├── requirements.txt
├── PRD.md
├── prompt.md
└── README.md
```

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure GCP Pub/Sub

Create a GCP project, enable the Pub/Sub API, then create a subscription:

```bash
gcloud pubsub subscriptions create trading-signals-sub --topic=trading-signals
```

Download a service account key JSON file, then configure `.env`:

```bash
cp .env.example .env
# Edit .env with your GCP project ID, subscription ID, and credentials path
```

### 3. Configure KIS API

```bash
cp trading/kis_devlp.yaml.example trading/kis_devlp.yaml
# Edit with your KIS app key, secret, and account number
```

Get credentials from [KIS Developers](https://apiportal.koreainvestment.com/).

## Usage

### Subscribe and execute trades

```bash
# Dry-run (log signals without trading)
python subscriber.py

# Live trading
python subscriber.py --execute
```

### Direct trading (without Pub/Sub)

```python
from trading.domestic_stock_trading import DomesticStockTrading

trader = DomesticStockTrading(mode="demo")
print(trader.get_current_price("005930"))
# trader.smart_buy("005930")  # uncomment to trade
```
