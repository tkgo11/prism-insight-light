# Playwright Installation Guide

> 📖 [한국어 문서](PLAYWRIGHT_SETUP_ko.md)

## 🎯 Installation for All Environments

PRISM-INSIGHT uses Playwright (Chromium browser) for PDF generation.

---

## 📦 Automatic Installation (Recommended)

The system will automatically attempt to download the browser on first run.

```bash
# Simply run - it will attempt automatic installation
python3 stock_analysis_orchestrator.py --mode afternoon
```

---

## 🔧 Manual Installation

If automatic installation fails, install manually:

### Method 1: Using Installation Script (Mac/Linux)

```bash
cd utils
chmod +x setup_playwright.sh
./setup_playwright.sh
```

### Method 2: Direct Commands

```bash
# Install Playwright package
pip install playwright

# Download Chromium browser
python3 -m playwright install chromium
```

### Method 3: With System Dependencies (Linux Server)

```bash
# Install Chromium with required system libraries
python3 -m playwright install --with-deps chromium
```

---

## 🐳 Docker Environment

In Docker, installation is **automatic**. No action required!

```bash
# Automatically installed during Docker build
docker-compose build

# Run container
docker-compose up -d
```

---

## 🖥️ Platform-Specific Installation

### macOS (Local Development)

```bash
# Install Python with Homebrew (optional)
brew install python3

# Install Playwright
pip3 install playwright
python3 -m playwright install chromium
```

### Rocky Linux 8 (Production Server)

```bash
# Python 3.9+ required
sudo dnf install python39

# Install Playwright
pip3 install playwright
python3 -m playwright install --with-deps chromium
```

### Ubuntu 24.04 (Docker or Local)

```bash
# Python 3.12 already included
pip install playwright
python3 -m playwright install --with-deps chromium
```

### Windows (WSL2)

```bash
# In WSL2 Ubuntu
sudo apt update
sudo apt install python3-pip
pip3 install playwright
python3 -m playwright install chromium
```

---

## ✅ Verify Installation

### Test Commands

```bash
# Verify in Python
python3 -c "from playwright.sync_api import sync_playwright; print('✅ Playwright OK')"

# Check browser version
python3 -m playwright --version
```

### Test PDF Conversion

```python
from pdf_converter import markdown_to_pdf

# Simple test
with open('test.md', 'w') as f:
    f.write('# Test Report\n\nThis is a test.')

markdown_to_pdf('test.md', 'test.pdf', method='playwright')
print('✅ PDF generation successful!')
```

---

## 🔍 Troubleshooting

### Error: "Executable doesn't exist"

**Cause**: Chromium browser not downloaded

**Solution**:
```bash
python3 -m playwright install chromium
```

### Error: "Playwright library is not installed"

**Cause**: playwright package not installed

**Solution**:
```bash
pip install playwright
```

### Error: "Missing dependencies" (Linux)

**Cause**: System libraries missing

**Solution**:
```bash
# Ubuntu/Debian
sudo apt-get install -y \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2

# Rocky/RHEL
sudo dnf install -y \
    nss nspr atk at-spi2-atk cups-libs libdrm libxkbcommon \
    libXcomposite libXdamage libXfixes libXrandr mesa-libgbm alsa-lib
```

### Docker Not Working

**Check**:
1. Verify `playwright install --with-deps chromium` in Dockerfile
2. Rebuild image: `docker-compose build --no-cache`

---

## 📊 Browser Size and Storage Location

- **Download Size**: ~150-200MB
- **Installation Location**:
  - **macOS**: `~/Library/Caches/ms-playwright/`
  - **Linux**: `~/.cache/ms-playwright/`
  - **Windows**: `%USERPROFILE%\AppData\Local\ms-playwright\`

---

## 🎉 Complete!

Now you can use PDF generation:

```bash
python3 stock_analysis_orchestrator.py --mode afternoon
```

For questions, please open an issue!
