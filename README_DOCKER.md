# 🐳 PRISM-INSIGHT Docker Installation Guide

> 📖 [한국어 문서](README_DOCKER_ko.md)

Run Ubuntu 24.04-based AI stock analysis system easily with Docker.

---

## 📋 Table of Contents
1. [System Configuration](#-system-configuration)
2. [Prerequisites](#-prerequisites)
3. [Installation and Execution](#-installation-and-execution)
4. [Configuration Files](#-configuration-files)
5. [Testing](#-testing)
6. [Usage](#-usage)
7. [Troubleshooting](#-troubleshooting)

---

## 🔧 System Configuration

### Components Included in Docker Image

#### System
- **OS**: Ubuntu 24.04 LTS
- **Python**: 3.12.x (virtual environment)
- **Node.js**: 22.x LTS
- **UV**: Python package manager
- **Playwright**: Chromium-based PDF generation (modern HTML to PDF converter)
- **Korean Fonts**: Nanum font family

#### Python Packages
- OpenAI API (GPT-4.1, GPT-5.1)
- Anthropic API (Claude Sonnet 4.5)
- MCP Agent and related servers
- pykrx (Korean stock data)
- matplotlib, seaborn (data visualization)
- All packages from project requirements.txt

#### MCP Servers
- **kospi-kosdaq**: Korean stock data
- **perplexity-ask**: AI search
- **firecrawl**: Web crawling
- **sqlite**: Database
- **time**: Time management

---

## 📦 Prerequisites

### 1. Check Docker Installation

```bash
# Check Docker version
docker --version

# Check Docker Compose version
docker-compose --version
```

If you don't have Docker:
- **Ubuntu**: https://docs.docker.com/engine/install/ubuntu/
- **macOS**: https://docs.docker.com/desktop/install/mac-install/
- **Windows**: https://docs.docker.com/desktop/install/windows-install/

### 2. System Requirements
- Docker 20.10 or later
- 4GB RAM or more
- 10GB free disk space

### 3. Required API Keys
- OpenAI API Key (https://platform.openai.com/api-keys)
- Anthropic API Key (https://console.anthropic.com/settings/keys)
- Perplexity API Key (https://www.perplexity.ai/settings/api)
- Firecrawl API Key (https://www.firecrawl.dev/)
- Telegram Bot Token (issued by [@BotFather](https://t.me/BotFather))
- Telegram Channel ID

---

## 🚀 Installation and Execution

### Overall Flow

```
1️⃣ Prepare configuration files on host (local)
   ↓
2️⃣ Run Docker Compose on host (local)
   ↓
3️⃣ Access container for testing
```

### Method 1: Using Docker Compose (Recommended)

#### Step 1: Prepare Configuration Files (on Host/Local)

Run in project root directory:

```bash
# Check current location (should be project root)
pwd
# Example: /home/user/prism-insight

# Create and edit .env file
cp .env.example .env
nano .env
# Or use your preferred editor: vi, vim, code, etc.

# Create and edit MCP config file
cp mcp_agent.config.yaml.example mcp_agent.config.yaml
nano mcp_agent.config.yaml

# Create and edit MCP secrets file
cp mcp_agent.secrets.yaml.example mcp_agent.secrets.yaml
nano mcp_agent.secrets.yaml
```

**Important**: This step must be done **before running the container** on your local computer!

#### Step 2: Run Docker Compose (on Host/Local)

```bash
# Build and run (in background)
docker-compose up -d --build

# Check logs (Ctrl+C to exit)
docker-compose logs -f

# Access container
docker-compose exec prism-insight /bin/bash
```

#### Step 3: Testing (Inside Container)

```bash
# Check Python version
python3 --version

# Check project directory
ls -la /app/prism-insight

# Check market business day
python3 check_market_day.py
```

### Method 2: Using Docker Commands Directly

All commands are executed on **host (local)**.

```bash
# Build image
docker build -t prism-insight:latest .

# Run container
docker run -it --name prism-insight-container \
  -v prism-data:/app/prism-insight/data \
  -v prism-db:/app/prism-insight \
  -v $(pwd)/reports:/app/prism-insight/reports \
  -v $(pwd)/pdf_reports:/app/prism-insight/pdf_reports \
  prism-insight:latest

# Access running container (in new terminal)
docker exec -it prism-insight-container /bin/bash
```

---

## ⚙️ Configuration Files

### 3 Required Configuration Files

#### 1. `.env` File
```bash
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_AI_BOT_TOKEN=your_ai_bot_token_here
TELEGRAM_CHANNEL_ID=@your_channel_id_here
```

#### 2. `mcp_agent.config.yaml` File
```yaml
$schema: ../../schema/mcp-agent.config.schema.json
execution_engine: asyncio
logger:
  type: console
  level: info
mcp:
  servers:
    firecrawl:
      command: "npx"
      args: [ "-y", "firecrawl-mcp" ]
      env:
        FIRECRAWL_API_KEY: "your_firecrawl_api_key_here"
    kospi_kosdaq:
      command: "python3"
      args: ["-m", "kospi_kosdaq_stock_server"]
    perplexity:
      command: "node"
      args: ["perplexity-ask/dist/index.js"]
      env:
        PERPLEXITY_API_KEY: "your_perplexity_api_key_here"
    sqlite:
      command: "uv"
      args: ["--directory", "sqlite", "run", "mcp-server-sqlite", "--db-path", "stock_tracking_db"]
    time:
      command: "uvx"
      args: ["mcp-server-time"]
openai:
  default_model: gpt-5.1
  reasoning_effort: high
```

#### 3. `mcp_agent.secrets.yaml` File
```yaml
$schema: ../../schema/mcp-agent.config.schema.json
openai:
  api_key: your_openai_api_key_here
anthropic:
  api_key: your_anthropic_api_key_here
```

### Security Notes
```bash
# Set file permissions
chmod 600 .env
chmod 600 mcp_agent.secrets.yaml

# Verify Git exclusion
cat .gitignore | grep -E "\.env|secrets"
```

---

## 🧪 Testing

Test with the following commands after accessing the container.

### 1. Basic Environment Test

```bash
# Check Python version (expected: 3.12.x)
python3 --version

# Check virtual environment (expected: /app/venv/bin/python)
which python

# Check main packages
pip list | grep -E "openai|anthropic|mcp-agent"

# Check Node.js
node --version
npm --version

# Check UV
uv --version
```

### 2. Korean Font Test

```bash
# List Korean fonts
fc-list | grep -i nanum

# Test Python Korean chart
python3 << 'EOF'
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

fonts = [f.name for f in fm.fontManager.ttflist if 'Nanum' in f.name]
print("Korean fonts:", fonts)

plt.rcParams['font.family'] = 'NanumGothic'
fig, ax = plt.subplots()
ax.plot([1, 2, 3], [1, 4, 9])
ax.set_title('Korean Test')
plt.savefig('/tmp/test_korean.png')
print("✅ Chart created: /tmp/test_korean.png")
EOF
```

### 3. Stock Data Query Test

```bash
python3 << 'EOF'
from pykrx import stock
from datetime import datetime, timedelta

today = datetime.now().strftime("%Y%m%d")
week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

try:
    df = stock.get_market_ohlcv(week_ago, today, "005930")
    print("✅ Samsung Electronics stock data query successful!")
    print(df.tail())
except Exception as e:
    print(f"⚠️ Error (may be weekend/holiday): {e}")
EOF
```

### 4. Project Script Test

```bash
# Check market business day
python3 check_market_day.py

# Check help
python3 stock_analysis_orchestrator.py --help
python3 trigger_batch.py --help
```

---

## 💻 Usage

### Execution Location Guide

- **🖥️ Host/Local**: Docker Compose commands
- **🐳 Inside Container**: Project execution commands

---

### Docker Compose Commands (on Host/Local)

```bash
# Start container
docker-compose up -d

# Stop container
docker-compose stop

# Restart container
docker-compose restart

# Check logs
docker-compose logs -f prism-insight

# Access container
docker-compose exec prism-insight /bin/bash

# Remove container
docker-compose down

# Remove including volumes
docker-compose down -v
```

### Project Execution (Inside Container)

```bash
# Navigate to project directory
cd /app/prism-insight

# Morning surge analysis
python3 stock_analysis_orchestrator.py --mode morning

# Afternoon surge analysis
python3 stock_analysis_orchestrator.py --mode afternoon

# Both morning + afternoon
python3 stock_analysis_orchestrator.py --mode both
```

### Crontab Automation Setup

```bash
# Inside container
chmod +x utils/setup_crontab_simple.sh
./utils/setup_crontab_simple.sh
```

### Data Backup (on Host/Local)

```bash
# Run on host
docker-compose exec prism-insight tar -czf /tmp/backup.tar.gz \
  stock_tracking_db.sqlite reports/ pdf_reports/

docker cp prism-insight-container:/tmp/backup.tar.gz \
  ./backup-$(date +%Y%m%d).tar.gz
```

---

## 🔧 Troubleshooting

### 1. Volume Mount Error (SQLite Database File)

**Error Message:**
```
failed to create task for container: failed to create shim task: OCI runtime create failed: 
error mounting "/root/prism-insight/stock_tracking_db.sqlite": not a directory
```

**Cause:** Docker cannot mount a file that doesn't exist on the host. The updated configuration uses Named Volumes instead.

**Solution:**
```bash
# The docker-compose.yml now uses Named Volume (prism-db)
# No manual file creation needed

# Access DB file inside container
docker-compose exec prism-insight ls -la /app/prism-insight/*.sqlite

# Backup DB to host
docker cp prism-insight-container:/app/prism-insight/stock_tracking_db.sqlite ./backup_db.sqlite
```

### 2. Configuration File Management

Configuration files are initially created inside the container.

**Options for editing:**

```bash
# Option 1: Edit directly in container (recommended for first-time setup)
docker-compose exec prism-insight nano /app/prism-insight/.env

# Option 2: Copy to host, edit, then copy back
docker cp prism-insight-container:/app/prism-insight/.env ./.env
# Edit on host
nano .env
# Copy back
docker cp ./.env prism-insight-container:/app/prism-insight/.env
docker-compose restart

# Option 3: Volume mount (after creating files on host)
# Uncomment these lines in docker-compose.yml:
# - ./.env:/app/prism-insight/.env
# - ./mcp_agent.config.yaml:/app/prism-insight/mcp_agent.config.yaml
# - ./mcp_agent.secrets.yaml:/app/prism-insight/mcp_agent.secrets.yaml
```

### 3. Command Execution Location

| Symptom/Task | Execution Location | Example |
|----------|----------|------|
| Docker build/run | 🖥️ Host/Local | `docker-compose up -d` |
| Access container | 🖥️ Host/Local | `docker-compose exec prism-insight /bin/bash` |
| Run Python scripts | 🐳 Inside Container | `python3 check_market_day.py` |
| Edit config files | 🖥️ Host/Local | `nano .env` |

---

### Build Failure (on Host/Local)

```bash
# Check Docker service
sudo systemctl status docker

# Restart Docker
sudo systemctl restart docker

# Rebuild without cache
docker-compose build --no-cache

# Or
docker build --no-cache -t prism-insight:latest .
```

### Korean Characters Garbled (Inside Container)

```bash
# Run inside container
fc-cache -fv
python3 ./cores/ubuntu_font_installer.py
python3 -c "import matplotlib.font_manager as fm; fm.fontManager.rebuild()"
```

### Virtual Environment Not Activated (Inside Container)

```bash
# Activate virtual environment
source /app/venv/bin/activate

# Verify
which python
# Expected output: /app/venv/bin/python
```

### API Key Recognition Error

```bash
# 1. Check config files on host/local
cat .env
cat mcp_agent.secrets.yaml

# 2. Verify proper mounting in container (on host/local)
docker-compose exec prism-insight cat /app/prism-insight/.env

# 3. Restart container (on host/local)
docker-compose restart
```

### Permission Issues (on Host/Local)

```bash
# On host
chmod -R 755 data reports pdf_reports
sudo chown -R $USER:$USER data reports pdf_reports
```

### Port Conflicts

```bash
# Change port in docker-compose.yml
# ports:
#   - "8080:8080"  # Change to another port
```

---

## 📊 Additional Information

### Container Internal Directory Structure

```
/app/
├── venv/                      # Python virtual environment
└── prism-insight/            # Project root
    ├── cores/                # AI analysis engine
    ├── trading/              # Automated trading
    ├── perplexity-ask/       # MCP server
    ├── sqlite/               # Database
    ├── reports/              # Analysis reports
    └── pdf_reports/          # PDF reports
```

### Image Information
- **Base Image**: ubuntu:24.04
- **Expected Size**: ~3-4GB
- **Build Time**: ~5-10 minutes (depending on network speed)

### Key Features
- ✅ Fully automated (Git clone ~ dependency installation)
- ✅ Perfect Korean support (Nanum fonts)
- ✅ MCP server integration
- ✅ Data persistence (volume mounting)
- ✅ Docker Compose support

---

## 📞 Support

- **Project**: https://github.com/dragon1086/prism-insight
- **Telegram**: https://t.me/stock_ai_agent
- **Issues**: https://github.com/dragon1086/prism-insight/issues

---

## ⚠️ Important Notes

- Never commit API keys to Git
- `.env` file is included in `.gitignore`
- Take appropriate security measures in production environments
- First build takes about 5-10 minutes

---

## 🔧 Path Configuration Information

The project uses **automatic path detection** so it works in any environment:

- **Local environment**: `~/my-path/prism-insight` ✅
- **Docker environment**: `/app/prism-insight` ✅
- **Other developers**: `/home/user/custom-path` ✅

Python executables are also auto-detected (priority):
1. Project virtual environment (`venv/bin/python`)
2. pyenv Python (`~/.pyenv/shims/python`)
3. System Python (`python3`)

---

**⭐ If this helped you, please star the GitHub repository!**
**License**: MIT | **Created by**: PRISM-INSIGHT Community
