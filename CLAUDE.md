# CLAUDE.md - AI Assistant Guide for PRISM-INSIGHT

> **Last Updated**: 2026-01-14
> **Version**: 1.3
> **Purpose**: Comprehensive guide for AI assistants working on the PRISM-INSIGHT codebase

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Codebase Structure](#3-codebase-structure)
4. [Development Workflows](#4-development-workflows)
5. [AI Agent System](#5-ai-agent-system)
6. [Key Conventions](#6-key-conventions)
7. [Configuration Management](#7-configuration-management)
8. [Testing Guidelines](#8-testing-guidelines)
9. [Common Tasks](#9-common-tasks)
10. [Important Constraints](#10-important-constraints)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Project Overview

### What is PRISM-INSIGHT?

PRISM-INSIGHT is a **production-grade AI-powered Korean stock market analysis and trading system** featuring:

- **13 specialized AI agents** collaborating for comprehensive stock analysis
- **Automated surge stock detection** with configurable criteria
- **Professional analyst-grade reports** in multiple languages (Korean, English, Japanese, Chinese)
- **Trading simulation** with real-world performance tracking
- **Automated trading** via Korea Investment & Securities (KIS) API
- **Telegram integration** for real-time distribution and user interaction

### Technology Stack

```yaml
Language: Python 3.10+
AI Framework: mcp-agent (Multi-Agent Orchestration)
LLM Models:
  - OpenAI GPT-5 (Analysis & Trading agents, default)
  - Anthropic Claude Sonnet 4.5 (Telegram bot)
Data Sources:
  - pykrx: Korean stock market data
  - MCP Servers: kospi_kosdaq, firecrawl, perplexity, deepsearch
Communication: python-telegram-bot (v20+)
Trading API: Korea Investment & Securities
Database: SQLite with aiosqlite (async)
PDF Generation: Playwright (Chromium-based)
Charts: matplotlib, seaborn, mplfinance
Messaging: Redis (Upstash), Google Cloud Pub/Sub (optional)
```

### Project Scale

- **~68+ Python files** | **~10,000+ lines of code**
- **13+ specialized AI agents** with distinct roles
- **Multiple entry points** for different workflows
- **Full async/await** throughout the codebase
- **Multi-language support** (ko, en, ja, zh, es, fr, de)
- **Event-driven architecture** with Redis/GCP Pub/Sub integration

---

## 2. Architecture

### High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    PRISM-INSIGHT SYSTEM                      │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐    ┌─────────────────┐   ┌─────────────┐ │
│  │   Trigger    │───▶│   Orchestrator  │──▶│   Analysis  │ │
│  │    Batch     │    │     Pipeline    │   │    Engine   │ │
│  └──────────────┘    └─────────────────┘   └─────────────┘ │
│         │                    │                      │        │
│         ▼                    ▼                      ▼        │
│  ┌──────────────┐    ┌─────────────────┐   ┌─────────────┐ │
│  │   Watchlist  │    │  13 AI Agents   │   │   Reports   │ │
│  │  Detection   │    │   Collaborate   │   │  (MD/PDF)   │ │
│  └──────────────┘    └─────────────────┘   └─────────────┘ │
│                              │                      │        │
│                              ▼                      ▼        │
│                      ┌─────────────────┐   ┌─────────────┐ │
│                      │    Trading      │   │  Telegram   │ │
│                      │   Simulation    │   │  Messages   │ │
│                      └─────────────────┘   └─────────────┘ │
│                              │                      │        │
│                              ▼                      ▼        │
│                      ┌─────────────────┐   ┌─────────────┐ │
│                      │   KIS Trading   │   │  Multi-lang │ │
│                      │      API        │   │  Channels   │ │
│                      └─────────────────┘   └─────────────┘ │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Main Execution Flow

```
Daily Trigger (09:10 Morning / 15:30 Afternoon)
    ↓
trigger_batch.py (Surge Stock Detection)
    ├─ Volume surge detection
    ├─ Gap detection (price jumps)
    └─ Sector performance analysis
    ↓
stock_analysis_orchestrator.py (Main Pipeline)
    ├─ 1. Send Prism Signal Alert (Telegram)
    ├─ 2. Generate AI Analysis Reports
    │   ├─ Sequential agent execution (rate limit friendly)
    │   └─ 13 specialized agents collaborate
    ├─ 3. Convert Markdown to PDF
    │   └─ Playwright (Chromium-based)
    ├─ 4. Generate Telegram Summaries
    │   ├─ Summary Optimizer Agent
    │   └─ Quality Evaluator Agent (iterative improvement)
    ├─ 5. Distribute to Telegram Channels
    │   └─ Multi-language broadcasting support
    └─ 6. Trading Simulation & Execution
        ├─ Buy/Sell Decision Agents
        ├─ Portfolio Management (max 10 slots)
        └─ KIS API Execution (demo or real mode)
```

---

## 3. Codebase Structure

### Directory Layout

```
prism-insight/
├── 📂 cores/                           # 🤖 Core AI Analysis Engine
│   ├── 📂 agents/                     # AI Agent Definitions
│   │   ├── company_info_agents.py     # Financial & Business Analysis
│   │   ├── stock_price_agents.py      # Technical & Flow Analysis
│   │   ├── news_strategy_agents.py    # News & Investment Strategy
│   │   ├── market_index_agents.py     # Market & Macro Analysis
│   │   ├── trading_agents.py          # Trading Scenario & Sell Decisions
│   │   ├── telegram_summary_optimizer_agent.py   # Summary Generation
│   │   ├── telegram_summary_evaluator_agent.py   # Quality Inspection
│   │   └── telegram_translator_agent.py          # Multi-language Support
│   ├── analysis.py                    # Core analysis orchestration
│   ├── report_generation.py           # Report templating & generation
│   ├── stock_chart.py                 # Chart generation (matplotlib)
│   ├── language_config.py             # Multi-language templates
│   └── utils.py                       # Utility functions
│
├── 📂 trading/                         # 💱 Automated Trading System
│   ├── domestic_stock_trading.py      # KIS API wrapper
│   ├── kis_auth.py                    # Authentication & token management
│   ├── portfolio_telegram_reporter.py # Portfolio reporting
│   ├── 📂 config/                     # Trading configurations
│   │   ├── kis_devlp.yaml.example     # KIS API config template
│   │   └── kis_devlp.yaml             # Actual config (gitignored)
│   └── 📂 samples/                    # API usage examples
│
├── 📂 examples/                        # Web Interfaces & Utilities
│   ├── 📂 streamlit/                  # Streamlit dashboard
│   │   ├── app_modern.py              # Main app
│   │   └── config.py.example          # Config template
│   ├── 📂 dashboard/                  # Next.js frontend
│   ├── 📂 messaging/                  # Event-driven trading signals
│   │   ├── redis_subscriber_example.py    # Redis/Upstash integration
│   │   └── gcp_pubsub_subscriber_example.py  # GCP Pub/Sub integration
│   ├── generate_dashboard_json.py     # Dashboard data generator
│   └── translation_utils.py           # Multi-language utilities
│
├── 📂 tests/                           # Test Suite
│   ├── test_async_trading.py          # Trading system tests
│   ├── test_tracking_agent.py         # Agent tests
│   ├── test_redis_signal_pubsub.py    # Redis signal tests
│   ├── test_gcp_pubsub_signal.py      # GCP Pub/Sub tests
│   ├── test_youtube_crawler.py        # YouTube crawler tests
│   ├── quick_test.py                  # Quick integration tests
│   └── test_*.py                      # Various unit tests
│
├── 📂 utils/                           # Utility Scripts
│   ├── setup_crontab.sh               # Crontab automation setup
│   ├── setup_playwright.sh            # Playwright installation
│   ├── backup_configs.sh              # Config & DB backup script
│   ├── migrate_lessons_to_principles.py        # Migration: lessons → principles
│   ├── migrate_watchlist_to_performance_tracker.py  # Migration: watchlist → tracker
│   └── CRONTAB_SETUP.md               # Setup documentation
│
├── 📂 perplexity-ask/                  # MCP Server (Node.js)
│   └── Perplexity search integration
│
├── 📂 sqlite/                          # SQLite MCP Server
│
├── 📂 docs/                            # Documentation & Images
│
├── stock_analysis_orchestrator.py     # 🎯 Main Orchestrator
├── report_generator.py                # Async report generation with global MCPApp
├── stock_tracking_agent.py            # Trading Simulation Agent
├── stock_tracking_enhanced_agent.py   # Enhanced Trading Agent (scipy stats)
├── telegram_ai_bot.py                 # Telegram Bot (Claude-based)
├── telegram_bot_agent.py              # Bot message handling
├── telegram_summary_agent.py          # Summary generation pipeline
├── run_telegram_pipeline.py           # Telegram message processing pipeline
├── trigger_batch.py                   # Surge Stock Detection
├── pdf_converter.py                   # Markdown → PDF conversion
├── telegram_config.py                 # TelegramConfig class
├── analysis_manager.py                # Background job queue
├── check_market_day.py                # Market holiday validation (incl. Dec 31)
├── update_stock_data.py               # Stock data update utility
├── compress_trading_memory.py         # Trading memory compression & cleanup
├── performance_tracker_batch.py       # Daily performance tracking batch job
│
├── requirements.txt                   # Python dependencies
├── .env.example                       # Environment variables template
├── mcp_agent.config.yaml.example      # MCP agent config template
├── mcp_agent.secrets.yaml.example     # MCP secrets template
├── Dockerfile                         # Docker container
└── docker-compose.yml                 # Docker orchestration
```

### Key Files Reference

| File | Purpose | When to Modify |
|------|---------|----------------|
| `stock_analysis_orchestrator.py` | Main pipeline orchestrator | Changing overall workflow |
| `report_generator.py` | Global MCPApp lifecycle management | Resource management, parallel processing |
| `cores/analysis.py` | Core analysis engine | Modifying agent collaboration |
| `cores/agents/*.py` | Individual agent definitions | Changing agent prompts/behavior |
| `cores/report_generation.py` | Report templates | Changing report format |
| `cores/utils.py` | Utility functions (markdown cleanup) | Output formatting fixes |
| `trading/domestic_stock_trading.py` | KIS API wrapper | Trading functionality changes |
| `stock_tracking_agent.py` | Trading decisions | Modifying trading strategy |
| `stock_tracking_enhanced_agent.py` | Enhanced trading with stats | Advanced trading signals |
| `trigger_batch.py` | Stock screening (v1.16.6: 고정 손절폭, 시총 5000억, 등락률 20%) | Changing detection criteria |
| `telegram_config.py` | Telegram configuration | Telegram settings |
| `pdf_converter.py` | PDF generation | PDF styling/formatting |
| `cores/language_config.py` | Multi-language templates | Adding/modifying languages |
| `examples/messaging/*.py` | Event-driven trading signals | Redis/GCP integration |
| `compress_trading_memory.py` | Memory compression & cleanup | Token optimization |
| `performance_tracker_batch.py` | Performance tracking | Analysis quality tracking |
| `utils/backup_configs.sh` | Config & DB backup | Backup automation |
| `utils/migrate_*.py` | Database migration scripts | Schema migrations |

---

## 4. Development Workflows

### Setting Up Development Environment

```bash
# 1. Clone repository
git clone https://github.com/dragon1086/prism-insight.git
cd prism-insight

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Install Playwright browsers
python3 -m playwright install chromium

# 4. Copy configuration templates
cp .env.example .env
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
cp ./examples/streamlit/config.py.example ./examples/streamlit/config.py
cp ./trading/config/kis_devlp.yaml.example ./trading/config/kis_devlp.yaml

# 5. Configure API keys in copied files
# Edit .env, mcp_agent.secrets.yaml, etc.

# 6. Install perplexity-ask MCP server (Node.js)
cd perplexity-ask && npm install && cd ..

# 7. Install Korean fonts (Linux only)
# Rocky Linux/CentOS: sudo dnf install google-nanum-fonts
# Ubuntu: Run ./cores/ubuntu_font_installer.py
sudo fc-cache -fv
python3 -c "import matplotlib.font_manager as fm; fm.fontManager.rebuild()"
```

### Running the System

```bash
# === Full Pipeline (Recommended) ===
# Morning analysis
python stock_analysis_orchestrator.py --mode morning

# Afternoon analysis
python stock_analysis_orchestrator.py --mode afternoon

# Both morning + afternoon
python stock_analysis_orchestrator.py --mode both

# Local testing without Telegram
python stock_analysis_orchestrator.py --mode morning --no-telegram

# === Individual Components ===
# Trigger batch only (surge stock detection)
python trigger_batch.py morning INFO --output trigger_results.json

# Generate analysis for specific stock
cd cores && python main.py

# Trading simulation
python stock_tracking_agent.py

# Telegram bot
python telegram_ai_bot.py

# === Multi-Language Broadcasting ===
# English report
python stock_analysis_orchestrator.py --mode morning --language en

# Multi-language broadcast
python stock_analysis_orchestrator.py --mode morning --broadcast-languages en,ja,zh
```

### Git Workflow

```bash
# Current branch naming convention
# Format: claude/claude-md-{session-id}

# Check current branch
git status

# Make changes and commit
git add .
git commit -m "feat: Add new feature description"

# Push to remote (use -u for first push)
git push -u origin claude/claude-md-mhyclezlwq2jq1tr-012iDsS6HRyXv357QdgTMNg2

# Create pull request
gh pr create --title "PR Title" --body "Description"
```

### Commit Message Conventions

Follow conventional commit format:

```
feat: Add new feature
fix: Bug fix
docs: Documentation changes
refactor: Code refactoring
test: Test additions/modifications
chore: Maintenance tasks
perf: Performance improvements
```

---

## 5. AI Agent System

> **Detailed documentation**: [docs/CLAUDE_AGENTS.md](docs/CLAUDE_AGENTS.md)

### Agent Overview

| Team | Agents | Model | Purpose |
|------|--------|-------|---------|
| Analysis | 6 agents | GPT-5 | Technical, Financial, Industry, News, Market analysis |
| Strategy | 1 agent | GPT-5 | Investment strategy synthesis |
| Communication | 3 agents | GPT-5 | Summary, Quality evaluation, Translation |
| Trading | 2 agents | GPT-5 | Buy/Sell decisions |
| Consultation | 2 agents | Claude 4.5 | User interaction via Telegram |

### Key Files

- `cores/agents/stock_price_agents.py` - Technical & Trading Flow
- `cores/agents/company_info_agents.py` - Financial & Industry
- `cores/agents/news_strategy_agents.py` - News & Strategy
- `cores/agents/trading_agents.py` - Buy/Sell decisions
- `telegram_ai_bot.py` - User consultation

---

## 6. Key Conventions

### Code Style

**Python Style Guide**:
- Follow PEP 8 conventions
- Use type hints where beneficial
- Docstrings for functions and classes
- Descriptive variable names
- Maximum line length: ~120 characters (flexible)

**Async/Await Pattern**:
```python
# ✅ Correct: Proper async pattern
async def analyze_stock(...):
    async with app.run() as parallel_app:
        result = await generate_report(agent, ...)
        return result

# ✅ Correct: Context manager for resources
async with AsyncTradingContext(mode="demo") as trader:
    result = await trader.async_buy_stock(stock_code)

# ❌ Incorrect: Blocking calls in async function
async def bad_example():
    result = requests.get(url)  # Blocks event loop!
    return result

# ✅ Correct: Use async libraries
async def good_example():
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.json()
```

**Logging Convention**:
```python
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"log_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Usage
logger.info("Starting analysis for %s", company_name)
logger.warning("Market data incomplete for %s", date)
logger.error("Failed to generate report: %s", e)
```

**Error Handling Pattern**:
```python
from tenacity import retry, stop_after_attempt, wait_exponential

# Graceful degradation
try:
    report = await generate_report(agent, section, ...)
    section_reports[section] = report
except Exception as e:
    logger.error(f"Error processing {section}: {e}", exc_info=True)
    section_reports[section] = f"Analysis unavailable: {section}"
    # Continue with next section

# Retry pattern for transient failures
@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=10, max=30),
)
async def generate_report(...):
    # Implementation
    pass
```

### File Naming

- Python files: `snake_case.py`
- Configuration: `*.yaml`, `*.yaml.example`
- Documentation: `*.md`, `UPPERCASE.md` for important docs
- Test files: `test_*.py`

### Variable Naming

```python
# Stock codes: 6 digits
stock_code = "005930"  # Samsung Electronics

# Company names: Korean or English
company_name = "삼성전자"
company_name_en = "Samsung Electronics"

# Dates: YYYY-MM-DD string format
reference_date = "2025-11-14"

# Language codes: ISO 639-1
language = "ko"  # or "en", "ja", "zh"

# Trading modes
mode = "demo"  # or "real"

# Paths: Use pathlib.Path
from pathlib import Path
report_path = Path("reports") / f"{company_code}_{reference_date}.md"
```

### Language Support Convention

```python
# Always support both Korean (default) and English
def create_agent(..., language: str = "ko"):
    if language == "en":
        instruction = """English prompt"""
    else:  # Korean default
        instruction = """한국어 프롬프트"""
    return Agent(instruction=instruction)

# Multi-language support
SUPPORTED_LANGUAGES = ["ko", "en", "ja", "zh", "es", "fr", "de"]

# Language-specific config
telegram_channels = {
    "ko": os.getenv("TELEGRAM_CHANNEL_ID"),
    "en": os.getenv("TELEGRAM_CHANNEL_ID_EN"),
    "ja": os.getenv("TELEGRAM_CHANNEL_ID_JA"),
    "zh": os.getenv("TELEGRAM_CHANNEL_ID_ZH"),
}
```

### Configuration Pattern

```python
# ✅ Correct: SOLID configuration class
class TelegramConfig:
    def __init__(self, use_telegram=True, broadcast_languages=None):
        self.use_telegram = use_telegram
        self.broadcast_languages = broadcast_languages or []
        self._load_env()

    def validate_or_raise(self):
        """Fail-fast validation"""
        if self.use_telegram and not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN required")

    def log_status(self):
        """Transparent logging"""
        logger.info(f"Telegram enabled: {self.use_telegram}")

# Usage
config = TelegramConfig(use_telegram=True)
config.validate_or_raise()
config.log_status()
```

---

## 7. Configuration Management

### Required Configuration Files

#### `.env` - Environment Variables (Telegram & General)

```bash
# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN="123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
TELEGRAM_AI_BOT_TOKEN="987654321:ZYXwvuTSRqponMLKjihgfEDcba"

# Telegram Channels
TELEGRAM_CHANNEL_ID="-1001234567890"        # Korean (main)
TELEGRAM_CHANNEL_ID_EN="-1001234567891"     # English
TELEGRAM_CHANNEL_ID_JA="-1001234567892"     # Japanese
TELEGRAM_CHANNEL_ID_ZH="-1001234567893"     # Chinese

# Optional: Default language
PRISM_LANGUAGE=ko  # or "en"

# Redis/Upstash (Optional - for event-driven signals)
UPSTASH_REDIS_REST_URL="https://xxx.upstash.io"
UPSTASH_REDIS_REST_TOKEN="your-token"

# GCP Pub/Sub (Optional - for event-driven signals)
GCP_PROJECT_ID="your-gcp-project"
GCP_PUBSUB_SUBSCRIPTION_ID="your-subscription"
GCP_CREDENTIALS_PATH="/path/to/service-account.json"
```

#### `mcp_agent.config.yaml` - MCP Agent Configuration

```yaml
execution_engine: asyncio

mcp:
  servers:
    # Web research and data fetching
    webresearch: npx @mzxrai/mcp-webresearch
    firecrawl: firecrawl-mcp
    perplexity: node perplexity-ask/dist/index.js

    # Deep search (remote MCP server)
    deepsearch:
      command: "npx"
      args: ["-y", "mcp-remote", "http://localhost:8000/sse"]

    # Stock market data
    kospi_kosdaq: python3 -m kospi_kosdaq_stock_server

    # Database
    sqlite: uv run mcp-server-sqlite --directory sqlite stock_tracking_db.sqlite

    # Utilities
    time: uvx mcp-server-time

openai:
  default_model: gpt-5
  reasoning_effort: medium  # Options: none, low, medium, high
```

#### `mcp_agent.secrets.yaml` - API Keys

```yaml
# OpenAI
OPENAI_API_KEY: "sk-..."

# Anthropic
ANTHROPIC_API_KEY: "sk-ant-..."

# Firecrawl
FIRECRAWL_API_KEY: "fc-..."

# Perplexity
PERPLEXITY_API_KEY: "pplx-..."

# WiseReport (Korean financial data)
WISEREPORT_KEY: "..."
```

#### `trading/config/kis_devlp.yaml` - Trading Configuration

```yaml
# Trading settings
default_unit_amount: 10000     # Buy amount per stock (KRW)
auto_trading: true             # Enable automated trading
default_mode: demo             # "demo" or "real"

# KIS API credentials
kis_app_key: "YOUR_APP_KEY"
kis_app_secret: "YOUR_APP_SECRET"
kis_account_number: "12345678-01"
kis_account_code: "01"         # Account type code

# Trading hours
market_open_time: "09:00"
market_close_time: "15:20"
```

#### `examples/streamlit/config.py` - Streamlit Dashboard

```python
# API Keys
OPENAI_API_KEY = "sk-..."
ANTHROPIC_API_KEY = "sk-ant-..."
FIRECRAWL_API_KEY = "fc-..."
PERPLEXITY_API_KEY = "pplx-..."
WISEREPORT_KEY = "..."

# Email configuration (optional)
SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587
SMTP_USERNAME = "your-email@gmail.com"
SMTP_PASSWORD = "your-app-password"
```

### Configuration Loading Pattern

```python
# 1. Load from environment variables
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")

# 2. Validate configuration
if not api_key:
    raise ValueError("OPENAI_API_KEY not found in .env")

# 3. Use configuration classes
from telegram_config import TelegramConfig

telegram_config = TelegramConfig(
    use_telegram=not args.no_telegram,
    broadcast_languages=args.broadcast_languages
)
telegram_config.validate_or_raise()
```

---

## 8. Testing Guidelines

### Test Structure

```
tests/
├── test_async_trading.py          # Trading system integration tests
├── test_tracking_agent.py         # Trading agent tests
├── test_portfolio_reporter.py     # Portfolio reporting tests
├── test_json_parsing.py           # JSON validation tests
├── test_parse_price_value.py      # Utility function tests
├── test_redis_signal_pubsub.py    # Redis signal integration tests
├── test_gcp_pubsub_signal.py      # GCP Pub/Sub signal tests
├── test_youtube_crawler.py        # YouTube event fund crawler tests
├── test_specific_functions.py     # Focused unit tests
├── quick_test.py                  # Quick integration tests
└── quick_json_test.py             # Rapid JSON validation
```

### Running Tests

```bash
# Run all tests
python -m pytest tests/

# Run specific test file
python -m pytest tests/test_async_trading.py

# Run with verbose output
python -m pytest tests/ -v

# Quick integration test
python tests/quick_test.py

# Trading system test
python tests/test_async_trading.py

# Redis signal test
python tests/test_redis_signal_pubsub.py

# GCP Pub/Sub signal test
python tests/test_gcp_pubsub_signal.py
```

### Writing Tests

**Unit Test Pattern**:
```python
import pytest
import asyncio

def test_price_parsing():
    """Test price value parsing utility"""
    from cores.utils import parse_price_value

    assert parse_price_value("1,234") == 1234
    assert parse_price_value("1,234.56") == 1234.56
    assert parse_price_value("N/A") is None
```

**Async Test Pattern**:
```python
import pytest
import asyncio

@pytest.mark.asyncio
async def test_async_trading():
    """Test async trading context"""
    from trading.domestic_stock_trading import AsyncTradingContext

    async with AsyncTradingContext(mode="demo", buy_amount=10000) as trader:
        # Test buy operation
        result = await trader.async_buy_stock("005930")
        assert result["success"] is True
```

**Integration Test Pattern**:
```python
# tests/quick_test.py
async def test_portfolio_check():
    """Quick portfolio check"""
    trader = DomesticStockTrading(mode="demo")
    portfolio = await trader.async_get_portfolio()

    logger.info(f"Portfolio: {len(portfolio)} positions")
    assert isinstance(portfolio, list)
```

### Testing Conventions

1. **Safety First**: Always use `mode="demo"` in tests
2. **Async Tests**: Use `@pytest.mark.asyncio` for async tests
3. **Logging**: Include logging for debugging
4. **Cleanup**: Properly close resources (use context managers)
5. **Mocking**: Mock external APIs when possible
6. **Real API Tests**: Mark as `@pytest.mark.integration` and skip by default

---

## 9. Common Tasks

> **Detailed documentation**: [docs/CLAUDE_TASKS.md](docs/CLAUDE_TASKS.md)

### Quick Reference

| Task | Command/File |
|------|--------------|
| Add new agent | `cores/agents/your_agent.py` → register in `__init__.py` |
| Modify surge detection | `trigger_batch.py` |
| Add language | `cores/language_config.py` + `.env` |
| Modify trading strategy | `cores/agents/trading_agents.py` |
| Customize report | `cores/report_generation.py` |
| Add MCP server | `mcp_agent.config.yaml` |
| Dashboard JSON | `python examples/generate_dashboard_json.py` |
| Memory compression | `python compress_trading_memory.py` |
| Performance migration | `python utils/migrate_watchlist_to_performance_tracker.py` |

---

## 10. Important Constraints

### API Rate Limits

**OpenAI Rate Limits**:
- **Sequential execution** required (not parallel) to respect rate limits
- Retry with exponential backoff on failures
- Market analysis cached to reduce calls

```python
# ✅ Correct: Sequential execution
for section in base_sections:
    report = await generate_report(agent, section, ...)
    section_reports[section] = report

# ❌ Incorrect: Parallel execution (will hit rate limits)
tasks = [generate_report(agent, section, ...) for section in base_sections]
reports = await asyncio.gather(*tasks)  # Don't do this!
```

**Retry Pattern**:
```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(2),
    wait=wait_exponential(multiplier=1, min=10, max=30),
)
async def generate_report(...):
    # Will retry up to 2 times with exponential backoff
    pass
```

### Trading Constraints

**Portfolio Constraints**:
```python
MAX_SLOTS = 10              # Maximum stocks to hold
MAX_SAME_SECTOR = 3         # Maximum stocks in same sector
SECTOR_CONCENTRATION = 0.3  # Maximum 30% in one sector
```

**Risk Management**:
```python
# v1.16.6: 고정 손절폭 방식 (Fixed Stop Loss)
# - 현재가 기준 고정 비율로 손절가 계산
# - 트리거 유형별 기준 적용 (일중 상승률: 5%, 기타: 7%)

# Stop Loss Rules (Trigger-Type Specific)
TRIGGER_CRITERIA = {
    "intraday_surge": {"sl_max": 0.05, "rr_target": 1.5},  # 일중 급등: -5%, R/R 1.5+
    "volume_surge": {"sl_max": 0.07, "rr_target": 2.0},    # 거래량 급증: -7%, R/R 2.0+
    "gap_up": {"sl_max": 0.07, "rr_target": 2.0},          # 갭상승: -7%, R/R 2.0+
    "default": {"sl_max": 0.07, "rr_target": 2.0},         # 기본: -7%, R/R 2.0+
}

# v1.16.6 손절가 계산 방식
stop_loss_price = current_price * (1 - sl_max)  # 현재가 기준 고정

# Target Price
TARGET_MIN = 0.10          # +10%
TARGET_TYPICAL = 0.20      # +20%
TARGET_MAX = 0.30          # +30%
TARGET_GUARANTEE = 0.15    # v1.16.6: 최소 +15% 보장

# Risk/Reward Ratio
MIN_RISK_REWARD_RATIO = 2.0  # Min 2:1 (20% target / 10% stop)

# v1.16.6 에이전트 적합도 점수 계산
agent_fit_score = rr_score * 0.6 + sl_score * 0.4  # 손익비 60% + 손절폭 40%
```

**Trading Mode Safety**:
```python
# Default mode: demo (safe)
DEFAULT_MODE = "demo"

# Real mode requires explicit confirmation
if mode == "real":
    confirm = input("⚠️  REAL TRADING MODE. Confirm? (yes/no): ")
    if confirm.lower() != "yes":
        logger.warning("Real trading cancelled by user")
        return
```

### Market Holiday Validation

**Holiday Check** (in `check_market_day.py`):
- Weekends (Saturday, Sunday)
- Korean public holidays
- **Year-end close (December 31)** - Added in recent update
- Other exchange-specific closures

```python
# check_market_day.py includes Dec 31 check
def is_market_day(date):
    # Check for year-end market close
    if date.month == 12 and date.day == 31:
        return False
    # ... other holiday checks
```

### Time-Based Data Accuracy

**Critical**: During market hours (09:00-15:20), today's data is incomplete!

```python
from datetime import datetime, time

def get_reliable_analysis_date():
    """
    Get reliable date for analysis.

    During market hours (09:00-15:20):
    - ❌ Today's volume/candles are INCOMPLETE
    - ✅ Use PREVIOUS day's data

    After market close (15:30+):
    - ✅ Today's data is COMPLETE and reliable
    """
    now = datetime.now()
    market_open = time(9, 0)
    market_close = time(15, 20)

    if market_open <= now.time() <= market_close:
        # During market hours: use previous day
        reference_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        logger.warning(f"Market hours: Using previous day {reference_date}")
    else:
        # After hours: today's data is complete
        reference_date = now.strftime("%Y-%m-%d")

    return reference_date
```

### Language Support

**Supported Languages**:
- Korean (ko): Default, most complete
- English (en): Full support
- Japanese (ja), Chinese (zh): Via translation
- Spanish (es), French (fr), German (de): Via translation

**Language Convention**:
```python
# Always provide Korean as fallback
def create_agent(..., language: str = "ko"):
    if language == "en":
        instruction = """English instruction"""
    else:  # Default to Korean
        instruction = """한국어 지시사항"""
    return Agent(instruction=instruction)
```

### Database Constraints

**SQLite with Async Support**:
```python
import aiosqlite

# ✅ Correct: Use aiosqlite for async operations
async with aiosqlite.connect("database.db") as db:
    async with db.execute("SELECT * FROM table") as cursor:
        rows = await cursor.fetchall()

# ❌ Incorrect: Don't use synchronous sqlite3 in async code
import sqlite3
conn = sqlite3.connect("database.db")  # Blocks event loop!
```

**Database Tables**:
- `stock_holdings`: Current portfolio
- `trading_history`: All trades (buy/sell)
- `watchlist_history`: Tracked stocks
- `holding_decisions`: Sell signals
- `market_condition`: Market analysis cache
- `trading_journal`: Trade journals with situation analysis and lessons
- `trading_principles`: Universal trading principles (extracted from lessons)
- `trading_intuitions`: Accumulated trading intuitions with confidence scores
- `analysis_performance_tracker`: 7/14/30-day performance tracking for analyzed stocks

---

## 11. Troubleshooting

> **Detailed documentation**: [docs/CLAUDE_TROUBLESHOOTING.md](docs/CLAUDE_TROUBLESHOOTING.md)

### Quick Fixes

| Issue | Solution |
|-------|----------|
| Playwright PDF fails | `python3 -m playwright install chromium` |
| Korean fonts missing | `sudo dnf install google-nanum-fonts` + `fc-cache -fv` |
| Telegram not responding | Check `.env`, verify bot token, check logs |
| MCP server fails | Verify `mcp_agent.config.yaml`, check API keys |
| KIS auth fails | Verify `trading/config/kis_devlp.yaml` |
| JSON parsing error | Use `json_repair` library |
| GPT-5 formatting | Use `cores/utils.clean_markdown()` |

### Debug Mode

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Getting Help

- **Logs**: `tail -f log_*.log`
- **GitHub**: [Issues](https://github.com/dragon1086/prism-insight/issues)
- **Telegram**: @stock_ai_ko

---

## Appendix: Quick Reference

### Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `TELEGRAM_BOT_TOKEN` | Main bot token | Yes (if using Telegram) |
| `TELEGRAM_AI_BOT_TOKEN` | AI bot token | Yes (if using AI bot) |
| `TELEGRAM_CHANNEL_ID` | Main channel (Korean) | Yes (if using Telegram) |
| `TELEGRAM_CHANNEL_ID_EN` | English channel | No |
| `TELEGRAM_CHANNEL_ID_JA` | Japanese channel | No |
| `TELEGRAM_CHANNEL_ID_ZH` | Chinese channel | No |
| `PRISM_LANGUAGE` | Default language | No (defaults to "ko") |
| `UPSTASH_REDIS_REST_URL` | Redis/Upstash URL | No (for event signals) |
| `UPSTASH_REDIS_REST_TOKEN` | Redis/Upstash token | No (for event signals) |
| `GCP_PROJECT_ID` | GCP project ID | No (for GCP signals) |
| `GCP_PUBSUB_SUBSCRIPTION_ID` | GCP Pub/Sub subscription | No (for GCP signals) |
| `GCP_CREDENTIALS_PATH` | GCP service account path | No (for GCP signals) |

### Command-Line Arguments

```bash
# stock_analysis_orchestrator.py
--mode {morning,afternoon,both}     # Analysis timing
--no-telegram                       # Skip Telegram integration
--language {ko,en}                  # Report language
--broadcast-languages ko,en,ja      # Multi-language broadcast

# trigger_batch.py
{morning,afternoon} {DEBUG,INFO,WARNING}  # Mode and log level
--output trigger_results.json       # Output file
```

### Key Directories

| Directory | Purpose |
|-----------|---------|
| `/cores/` | Core analysis engine and agents |
| `/trading/` | Trading system and KIS API |
| `/examples/` | Web interfaces (Streamlit, Next.js), messaging |
| `/examples/messaging/` | Event-driven trading signal examples |
| `/tests/` | Test suite |
| `/utils/` | Utility scripts and setup tools |
| `/docs/` | Documentation and images |

### Port Numbers

| Service | Port |
|---------|------|
| Streamlit Dashboard | 8501 |
| Next.js Dashboard | 3000 |

---

**Document Version**: 1.3
**Last Updated**: 2026-01-14
**Maintained By**: PRISM-INSIGHT Development Team

### Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.3 | 2026-01-14 | Trigger Batch Algorithm v3.0 (v1.16.6) - 고정 손절폭 방식, 시총 필터 5000억, 등락률 필터 20%, 에이전트 적합도 점수 시스템 |
| 1.2 | 2026-01-11 | Trading Journal Memory system (v1.16.0), Universal Principles, Token Cleanup, Trading Insights Dashboard, Performance Tracker |
| 1.1 | 2026-01-03 | GPT-5 upgrade, Redis/GCP Pub/Sub integration, new files documentation |
| 1.0 | 2025-11-14 | Initial comprehensive documentation |

For questions or improvements to this document, please submit a GitHub issue or pull request.
