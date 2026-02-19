# PRISM INSIGHT - Runbook

## Deployment

### Prerequisites
-   Python 3.10+
-   Google Cloud Pub/Sub Credentials
-   KIS API Credentials

### Installation
1.  **Clone Repository**
    ```bash
    git clone <repo_url>
    cd prism-insight-light
    ```
2.  **Install Dependencies**
    ```bash
    pip install -r requirements.txt
    ```
3.  **Environment Setup**
    -   Create `.env` file with:
        -   `GCP_PROJECT_ID`
        -   `GCP_PUBSUB_SUBSCRIPTION_ID`
        -   `SLACK_WEBHOOK_URL` (Optional)
        -   `DISCORD_WEBHOOK_URL` (Optional)
    -   Ensure `kis_devlp.yaml` (or `kis_real.yaml`) is present in `trading/config/`.

### Starting the Bot
```bash
# Real Mode
python subscriber.py

# Simulation Mode
python subscriber.py --dry-run
```

### Starting the Dashboard
```bash
python dashboard.py
# Access at http://localhost:8000
```

## Monitoring & Troubleshooting

### Logs
-   Application logs are printed to stdout.
-   Trade logs are stored in `trading.db` and visible on the dashboard.

### Common Issues
1.  **"Token file permission error"**
    -   Run `icacls trading\token.dat /inheritance:r /grant:r %USERNAME%:F` (Windows).
2.  **"Dashboard won't start"**
    -   Ensure port 8000 is free or change port in `dashboard.py`.
3.  **"KIS Rate Limit Exceeded"**
    -   The internal rate limiter handles this, but if multiple instances run, they do not share state. Run only ONE instance of `subscriber.py` per account.

## Maintenance
-   **Database**: `trading.db` is a simple SQLite file. Backup regularly if data importance is high.
-   **Updates**: Pull latest code and run `pip install -r requirements.txt`.
