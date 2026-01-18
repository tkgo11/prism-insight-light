# US Stock Analysis Orchestrator - Pipeline Test Checklist

> **Created**: 2026-01-18
> **Last Updated**: 2026-01-19
> **Purpose**: Step-by-step testing of us_stock_analysis_orchestrator.py pipeline
> **Test Mode**: Interactive manual testing with Claude

---

## Pre-requisites Checklist

| # | Item | Command | Status |
|---|------|---------|--------|
| 0.1 | Python environment ready | `python --version` | [ ] |
| 0.2 | Required packages installed | `pip list \| grep -E "yfinance\|mcp"` | [ ] |
| 0.3 | .env file configured | `ls -la .env` | [ ] |
| 0.4 | MCP config ready | `ls -la mcp_agent.config.yaml` | [ ] |
| 0.5 | US market day check | `python prism-us/check_market_day.py` | [ ] |

---

## Pipeline Steps Overview

```
┌─────────────────────────────────────────────────────────────┐
│              US Stock Analysis Pipeline                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  Step 1: Trigger Batch                                       │
│     └─> Detect surge stocks (volume, gap, intraday)         │
│                    │                                         │
│                    ▼                                         │
│  Step 2: Trigger Alert (Optional)                           │
│     └─> Send initial alert to Telegram                      │
│                    │                                         │
│                    ▼                                         │
│  Step 3: Report Generation                                   │
│     └─> AI agents analyze each stock (6 sections)           │
│                    │                                         │
│                    ▼                                         │
│  Step 4: PDF Conversion                                      │
│     └─> Markdown → PDF via Playwright                       │
│                    │                                         │
│                    ▼                                         │
│  Step 5: Telegram Summary                                    │
│     └─> Generate concise summary messages                   │
│                    │                                         │
│                    ▼                                         │
│  Step 6: Send to Telegram (Optional)                        │
│     └─> Distribute messages and PDFs                        │
│                    │                                         │
│                    ▼                                         │
│  Step 7: Trading Simulation                                  │
│     └─> Buy/Sell decisions via AI agents                    │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Step 1: Trigger Batch Test

### Description
- **Purpose**: Detect surge stocks based on volume, gap, intraday rise criteria
- **Input**: Market mode (morning/afternoon)
- **Output**: JSON file with selected tickers

### Test Command
```bash
cd /Users/aerok/Desktop/rocky/prism-insight/prism-insight
python prism-us/us_trigger_batch.py morning INFO --output test_trigger_step1.json
```

### Expected Output
- [ ] No Python errors
- [ ] JSON file created: `test_trigger_step1.json`
- [ ] JSON contains `metadata` section
- [ ] JSON contains at least one trigger type (e.g., `volume_surge`, `gap_up`)
- [ ] Each stock has: ticker, name, current_price, change_rate

### Verification
```bash
# Check if file exists and view contents
cat test_trigger_step1.json | python -m json.tool | head -50
```

### Status: [ ] PASS / [ ] FAIL

---

## Step 2: Trigger Alert Test (Optional - Requires Telegram)

### Description
- **Purpose**: Send immediate alert to Telegram when stocks are detected
- **Input**: Trigger results JSON file
- **Output**: Telegram message sent

### Test Command
```python
# Skip if not using Telegram
# This step is tested as part of Step 6
```

### Status: [ ] SKIP / [ ] PASS / [ ] FAIL

---

## Step 3: Report Generation Test

### Description
- **Purpose**: Generate comprehensive AI analysis report for a single stock
- **Input**: Ticker, company name, reference date
- **Output**: Markdown report file

### Test Command
```bash
cd /Users/aerok/Desktop/rocky/prism-insight/prism-insight
python -c "
import asyncio
import sys
sys.path.insert(0, 'prism-us')
from cores.us_analysis import analyze_us_stock
from check_market_day import get_reference_date

async def test():
    ref_date = get_reference_date()  # Auto-detect last trading day
    print(f'Reference date: {ref_date}')
    print('Starting analysis for AAPL...')
    report = await analyze_us_stock(
        ticker='AAPL',
        company_name='Apple Inc.',
        reference_date=ref_date,
        language='en'
    )
    if report:
        print(f'Report generated: {len(report)} characters')
        with open('prism-us/reports/test_AAPL_step3.md', 'w') as f:
            f.write(report)
        print('Saved to: prism-us/reports/test_AAPL_step3.md')
    else:
        print('ERROR: Empty report generated')

asyncio.run(test())
"
```

### Expected Output
- [ ] No Python errors
- [ ] Report file created: `prism-us/reports/test_AAPL_step3.md`
- [ ] Report contains section "# 1-1. Price and Volume Analysis"
- [ ] Report contains section "# 1-2. Institutional Ownership Analysis"
- [ ] Report contains section "# 2-1. Company Status Analysis"
- [ ] Report contains section "# 2-2. Company Overview Analysis"
- [ ] Report contains section "# 3. Recent Major News Summary"
- [ ] Report contains section "# 4. Market Analysis"
- [ ] Report length > 5000 characters

### Verification
```bash
# Check report structure
head -100 prism-us/reports/test_AAPL_step3.md
wc -c prism-us/reports/test_AAPL_step3.md
grep -E "^# [0-9]" prism-us/reports/test_AAPL_step3.md
```

### Status: [ ] PASS / [ ] FAIL

---

## Step 4: PDF Conversion Test

### Description
- **Purpose**: Convert markdown report to PDF using Playwright
- **Input**: Markdown file from Step 3
- **Output**: PDF file

### Test Command
```bash
cd /Users/aerok/Desktop/rocky/prism-insight/prism-insight
python -c "
from pdf_converter import markdown_to_pdf
from pathlib import Path

input_file = 'prism-us/reports/test_AAPL_step3.md'
output_file = 'prism-us/pdf_reports/test_AAPL_step4.pdf'

Path('prism-us/pdf_reports').mkdir(exist_ok=True)

print(f'Converting {input_file} to PDF...')
markdown_to_pdf(input_file, output_file, 'playwright', add_theme=True, enable_watermark=False)
print(f'PDF created: {output_file}')
"
```

### Expected Output
- [ ] No Python errors
- [ ] PDF file created: `prism-us/pdf_reports/test_AAPL_step4.pdf`
- [ ] PDF file size > 50KB
- [ ] PDF is viewable (not corrupted)

### Verification
```bash
ls -la prism-us/pdf_reports/test_AAPL_step4.pdf
file prism-us/pdf_reports/test_AAPL_step4.pdf
```

### Status: [ ] PASS / [ ] FAIL

---

## Step 5: Telegram Summary Generation Test

### Description
- **Purpose**: Generate concise Telegram summary from PDF report
- **Input**: PDF file from Step 4
- **Output**: Text file with Telegram message

### Test Command
```bash
cd /Users/aerok/Desktop/rocky/prism-insight/prism-insight
python -c "
import asyncio
import sys
sys.path.insert(0, 'prism-us')
from us_telegram_summary_agent import USTelegramSummaryGenerator
from pathlib import Path

async def test():
    output_dir = 'prism-us/telegram_messages'
    Path(output_dir).mkdir(exist_ok=True)

    generator = USTelegramSummaryGenerator()
    print('Generating Telegram summary...')
    await generator.process_report(
        'prism-us/pdf_reports/test_AAPL_step4.pdf',
        output_dir,
        language='en'
    )
    print(f'Summary generated in: {output_dir}')

asyncio.run(test())
"
```

### Expected Output
- [ ] No Python errors
- [ ] Summary file created in `prism-us/telegram_messages/`
- [ ] Summary contains stock name and ticker
- [ ] Summary contains key metrics (price, change rate)
- [ ] Summary length between 500-2000 characters (Telegram limit friendly)

### Verification
```bash
ls -la prism-us/telegram_messages/
cat prism-us/telegram_messages/*AAPL*.txt 2>/dev/null || echo "Check filename pattern"
```

### Status: [ ] PASS / [ ] FAIL

---

## Step 6: Telegram Send Test (Optional)

### Description
- **Purpose**: Send summary message and PDF to Telegram channel
- **Input**: Summary text file, PDF file
- **Output**: Messages appear in Telegram channel

### Pre-requisites
- [ ] TELEGRAM_BOT_TOKEN configured in .env
- [ ] TELEGRAM_CHANNEL_ID_EN configured in .env
- [ ] Bot has permission to post in channel

### Test Command
```bash
# Only run if Telegram is configured
cd /Users/aerok/Desktop/rocky/prism-insight/prism-insight
python -c "
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

# Check if Telegram is configured
token = os.getenv('TELEGRAM_BOT_TOKEN')
channel = os.getenv('TELEGRAM_CHANNEL_ID_EN') or os.getenv('TELEGRAM_CHANNEL_ID')

if not token or not channel:
    print('SKIP: Telegram not configured')
    exit(0)

print(f'Telegram configured: channel={channel[:10]}...')

from telegram_bot_agent import TelegramBotAgent

async def test():
    bot = TelegramBotAgent()

    # Send test message
    test_msg = '[TEST] US Pipeline Test - Step 6 verification'
    success = await bot.send_message(channel, test_msg)
    print(f'Test message sent: {success}')

asyncio.run(test())
"
```

### Expected Output
- [ ] No Python errors (or graceful skip)
- [ ] Test message appears in Telegram channel
- [ ] No authentication errors

### Status: [ ] SKIP / [ ] PASS / [ ] FAIL

---

## Step 7: Trading Simulation Test

### Description
- **Purpose**: Run AI trading agents to make buy/sell decisions
- **Input**: PDF reports, trigger results
- **Output**: Trading decisions logged, portfolio updated

### Test Command
```bash
cd /Users/aerok/Desktop/rocky/prism-insight/prism-insight
python -c "
import asyncio
import sys
sys.path.insert(0, 'prism-us')
from us_stock_tracking_agent import USStockTrackingAgent, app

async def test():
    print('Initializing US Stock Tracking Agent...')

    async with app.run():
        agent = USStockTrackingAgent(telegram_token=None)

        # Use test PDF from Step 4
        pdf_paths = ['prism-us/pdf_reports/test_AAPL_step4.pdf']

        print('Running trading simulation (no Telegram)...')
        result = await agent.run(
            pdf_paths,
            chat_id=None,  # No Telegram
            language='en',
            trigger_results_file='test_trigger_step1.json'
        )

        print(f'Trading simulation result: {result}')

asyncio.run(test())
"
```

### Expected Output
- [ ] No Python errors
- [ ] Agent initializes successfully
- [ ] Trading decision made (buy/hold/skip)
- [ ] Result logged to console

### Verification
```bash
# Check trading database for new entries
sqlite3 stock_tracking_db.sqlite "SELECT * FROM us_stock_holdings ORDER BY created_at DESC LIMIT 5;"
```

### Status: [ ] PASS / [ ] FAIL

---

## Full Pipeline Test (Integration)

### Description
- **Purpose**: Run complete pipeline end-to-end
- **Input**: Mode (morning/afternoon)
- **Output**: All steps completed successfully

### Test Command
```bash
cd /Users/aerok/Desktop/rocky/prism-insight/prism-insight

# Without Telegram
python prism-us/us_stock_analysis_orchestrator.py --mode morning --no-telegram --language en
```

### Expected Output
- [ ] Pipeline starts without errors
- [ ] Trigger batch executes
- [ ] At least 1 stock analyzed (or graceful exit if none found)
- [ ] PDF generated (if stocks found)
- [ ] Trading simulation runs
- [ ] Pipeline completes with success message

### Status: [ ] PASS / [ ] FAIL

---

## Test Results Summary

| Step | Name | Status | Notes |
|------|------|--------|-------|
| 0 | Pre-requisites | | |
| 1 | Trigger Batch | | |
| 2 | Trigger Alert | SKIP | |
| 3 | Report Generation | | |
| 4 | PDF Conversion | | |
| 5 | Telegram Summary | | |
| 6 | Telegram Send | SKIP | |
| 7 | Trading Simulation | | |
| Full | Integration Test | | |

---

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: mcp_agent` | `pip install mcp-agent` |
| `Playwright not installed` | `python -m playwright install chromium` |
| `Korean fonts missing` | Install Nanum fonts |
| `yfinance rate limit` | Wait 1-2 minutes and retry |
| `SEC EDGAR 403 error` | Rate limit (10 req/sec), wait 10 minutes |
| `Empty trigger results` | Normal if market closed or no surge stocks |
| `Telegram auth error` | Check bot token and channel permissions |

### MCP Server Configuration

MCP servers are executed via `uvx` (remote from PyPI):

```yaml
yahoo_finance:
  command: "uvx"
  args: ["yahoo-finance-mcp"]

sec_edgar:
  command: "uvx"
  args: ["sec-edgar-mcp"]
  env:
    SEC_EDGAR_USER_AGENT: "PRISM-INSIGHT (prism-insight@github.com)"
```

---

**Tester**: Claude + User
**Date**: 2026-01-18 ~ 2026-01-19
