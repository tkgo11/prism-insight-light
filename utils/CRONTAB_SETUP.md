# 📅 PRISM-INSIGHT Crontab Setup Guide

> 📖 [한국어 문서](CRONTAB_SETUP_ko.md)

## Overview
PRISM-INSIGHT uses crontab to automate stock market analysis. This document explains how to set up automatic execution schedules on your system.

## 🚀 Quick Start

### 1. Simple Setup (Recommended)
```bash
# Grant execution permission
chmod +x setup_crontab_simple.sh

# Run script
./setup_crontab_simple.sh
```

### 2. Advanced Setup
```bash
# Grant execution permission
chmod +x setup_crontab.sh

# Interactive setup
./setup_crontab.sh

# Or automatic setup using environment variables
PROJECT_DIR=/opt/prism-insight PYTHON_PATH=/usr/bin/python3 ./setup_crontab.sh --non-interactive
```

## 📋 Execution Schedule

### Default Schedule (Korea Time)

| Time | Task | Description |
|------|------|------|
| 07:00 | Data Update | Update stock information before market opens |
| 09:30 | Morning Analysis | Detect and analyze surging stocks after market opens |
| 15:40 | Afternoon Analysis | Comprehensive analysis after market closes |
| 03:00 | Log Cleanup | Delete old log files |
| 18:00 | Portfolio Report | (Optional) Daily trading performance report |

### Schedule Details

#### 1. **Morning Analysis (09:30)**
- Based on 10-minute data after market opens
- Detect gap-up and volume surge stocks
- Real-time market trend analysis

#### 2. **Afternoon Analysis (15:40)**
- Comprehensive analysis after market closes
- Analyze intraday gains and closing strength
- Generate detailed AI reports

#### 3. **Data Update (07:00)**
- Update stock master information
- Collect previous day's trading data
- Check system readiness

#### 4. **Log Cleanup (03:00)**
- Delete logs older than 30 days
- Manage disk space
- System optimization

## 🛠️ Manual Setup

### 1. Edit Crontab
```bash
crontab -e
```

### 2. Set Environment Variables
```bash
SHELL=/bin/bash
PATH=/usr/local/bin:/usr/bin:/bin
PYTHONPATH=/path/to/prism-insight
```

### 3. Add Schedules
```bash
# Morning analysis (Mon-Fri)
30 9 * * 1-5 cd /path/to/prism-insight && python stock_analysis_orchestrator.py --mode morning >> logs/morning.log 2>&1

# Afternoon analysis (Mon-Fri)
40 15 * * 1-5 cd /path/to/prism-insight && python stock_analysis_orchestrator.py --mode afternoon >> logs/afternoon.log 2>&1

# Data update (Mon-Fri)
0 7 * * 1-5 cd /path/to/prism-insight && python update_stock_data.py >> logs/update.log 2>&1

# Log cleanup (daily)
0 3 * * * cd /path/to/prism-insight && utils/cleanup_logs.sh
```

## 🔧 Environment-Specific Setup

### Rocky Linux / CentOS / RHEL
```bash
# Set SELinux context (if needed)
sudo semanage fcontext -a -t bin_t "/path/to/prism-insight/.*\.py"
sudo restorecon -Rv /path/to/prism-insight/

# Firewall settings (when using Telegram bot)
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

### Ubuntu / Debian
```bash
# When using system Python
sudo apt-get install python3-venv python3-pip

# Set permissions
chmod +x *.sh
chmod +x *.py
```

### macOS
```bash
# Recommend using Homebrew Python
brew install python3

# Use launchd (instead of crontab)
# Create ~/Library/LaunchAgents/com.prism-insight.plist
```

## 🐍 Python Environment-Specific Setup

### Using pyenv
```bash
# When .python-version file exists
PYTHON_PATH="$HOME/.pyenv/shims/python"
```

### Using venv
```bash
# Run after activating virtual environment
source /path/to/venv/bin/activate && python script.py
```

### Using conda
```bash
# Activate conda environment
eval "$(conda shell.bash hook)"
conda activate prism-insight
```

## 📊 Log Checking

### Real-time Log Monitoring
```bash
# Morning analysis log
tail -f logs/stock_analysis_morning_$(date +%Y%m%d).log

# Afternoon analysis log
tail -f logs/stock_analysis_afternoon_$(date +%Y%m%d).log

# Check all logs
tail -f logs/*.log
```

### Log Analysis
```bash
# Check today's errors
grep ERROR logs/*$(date +%Y%m%d)*.log

# Number of successful analyses
grep "분석 완료" logs/*.log | wc -l

# Last 5 days log summary
for i in {0..4}; do
    date -d "$i days ago" +%Y%m%d
    grep -c "완료" logs/*$(date -d "$i days ago" +%Y%m%d)*.log
done
```

## 🔍 Troubleshooting

### 1. Crontab Not Running
```bash
# Check cron service
sudo systemctl status crond  # RHEL/CentOS
sudo systemctl status cron   # Ubuntu/Debian

# Restart service
sudo systemctl restart crond
```

### 2. Python Not Found
```bash
# Check PATH
which python3

# Use full path in crontab
/usr/bin/python3 script.py
```

### 3. Permission Errors
```bash
# Grant execution permission
chmod +x *.py *.sh

# Check ownership
ls -la

# Change ownership if needed
chown -R $USER:$USER /path/to/prism-insight
```

### 4. Timezone Issues
```bash
# Check system timezone
timedatectl

# Set Korea timezone
sudo timedatectl set-timezone Asia/Seoul

# Specify timezone in crontab
TZ=Asia/Seoul
30 9 * * 1-5 command
```

## 📝 Maintenance

### Backup
```bash
# Backup crontab
crontab -l > crontab_backup_$(date +%Y%m%d).txt

# Restore
crontab crontab_backup_20250113.txt
```

### Temporary Pause
```bash
# Stop all
crontab -r

# Comment out specific task only
crontab -e
# 30 9 * * 1-5 ...  <- Add #
```

### Testing
```bash
# Manual execution test
cd /path/to/prism-insight
python stock_analysis_orchestrator.py --mode morning

# Simulate cron environment
env -i SHELL=/bin/bash PATH=/usr/bin:/bin python script.py

# Check next execution time
crontab -l | grep -v "^#" | cut -f 1-5 -d ' ' | while read schedule; do
    echo "$schedule -> $(date -d "$schedule" 2>/dev/null || echo "Daily/Weekly repeat")"
done
```

## 🎯 Best Practices

### 1. **Setup Log Rotation**
```bash
# Create /etc/logrotate.d/prism-insight
/path/to/prism-insight/logs/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 640 user group
    sharedscripts
}
```

### 2. **Setup Error Notifications**
```bash
# Email notification on error
MAILTO=your-email@example.com
30 9 * * 1-5 /path/to/script.py || echo "Morning analysis failed" | mail -s "PRISM-INSIGHT Error" $MAILTO
```

### 3. **Health Check**
```bash
# Execution status monitoring script
#!/bin/bash
# health_check.sh

LAST_RUN=$(find logs -name "*$(date +%Y%m%d)*.log" -mmin -60 | wc -l)
if [ $LAST_RUN -eq 0 ]; then
    echo "Warning: No execution record within the last hour"
    # Notification logic
fi
```

### 4. **Resource Limits**
```bash
# CPU/memory usage limits
30 9 * * 1-5 nice -n 10 ionice -c 3 timeout 3600 python script.py

# nice: Lower CPU priority
# ionice: Lower I/O priority
# timeout: Maximum execution time limit (1 hour)
```

## 📚 References

### Cron Expression Guide

| Field | Values | Description |
|------|-----|------|
| Minute | 0-59 | On the hour: 0 |
| Hour | 0-23 | 9 AM: 9 |
| Day | 1-31 | Every day: * |
| Month | 1-12 | Every month: * |
| Day of Week | 0-7 | Mon-Fri: 1-5 (0,7=Sunday) |

### Special Characters

- `*` : All values
- `,` : Value list (e.g., 1,3,5)
- `-` : Range (e.g., 1-5)
- `/` : Interval (e.g., */5 = every 5 minutes)

### Useful Examples

```bash
# Every 30 minutes
*/30 * * * * command

# Every hour from 9 AM to 6 PM on weekdays
0 9-18 * * 1-5 command

# Every Monday at 8 AM
0 8 * * 1 command

# 1st and 15th of every month
0 0 1,15 * * command

# Quarterly (1st of Jan, Apr, Jul, Oct)
0 0 1 1,4,7,10 * command
```

## ⚠️ Important Notes

1. **Market Holiday Handling**
   - Implement holiday check logic inside scripts
   - Reference KRX market closure calendar

2. **Timezone Settings**
   - Adjust time if server timezone is not KST
   - For UTC servers: Consider 9-hour difference

3. **Permission Management**
   - Use environment variables for sensitive information (API keys, etc.)
   - Be careful not to expose personal information in log files

4. **Backup Policy**
   - Regular database backups
   - Archive important logs

## 🤝 Getting Help

If you encounter problems or need assistance:

1. Inquire on [GitHub Issues](https://github.com/yourusername/prism-insight/issues)
2. Get community support on [Telegram channel](https://t.me/stock_ai_agent)
3. Check log files (`logs/` directory)
4. Refer to the troubleshooting section in this document

---

*Last Updated: January 2025*
