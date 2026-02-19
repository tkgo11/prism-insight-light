# PRISM INSIGHT - API Documentation

## Core Modules

### 1. `subscriber.py`
The main entry point for the trading bot.
-   **Modes**:
    -   `LIVE`: Real execution based on signals.
    -   `DRY-RUN`: Simulation mode (logging only).
    -   `DEMO`: Real execution during market hours, scheduled execution off-hours.
-   **Key Components**:
    -   `ScheduledOrderManager` (SQLite-backed)
    -   `MarketDataBuffer` (Circular buffer for analysis)
    -   `NotifierManager` (Slack/Discord)

### 2. `trading/database.py`
Database layer using SQLAlchemy and SQLite.
-   **Models**:
    -   `ScheduledOrder`: Stores off-hour orders.
    -   `TradeLog`: Persistent log of executed trades.
-   **Functions**:
    -   `init_db()`: Initializes tables.
    -   `get_db()`: Dependency for DB sessions.

### 3. `trading/analysis.py`
Market data analysis tools.
-   **Classes**:
    -   `MarketDataBuffer`: Stores last N prices to calculate volatility and moving averages.

### 4. `dashboard.py`
FastAPI web interface.
-   **Endpoints**:
    -   `GET /`: Main dashboard UI.
    -   `GET /api/orders`: JSON list of scheduled orders.
    -   `GET /api/logs`: JSON list of trade logs.

## Trading Logic

### `DomesticStockTrading` & `USStockTrading`
-   Inherit from `BaseStockTrading`.
-   Use `KISAuthManager` for token management and `aiohttp` for async I/O.
-   Implements `KISRateLimiter` (token bucket) to respect API limits.

## Testing
-   `tests/mock_kis_api.py`: Simulates KIS API for offline testing.
-   `tests/test_integration.py`: Verifies full trading flow using mock API.
