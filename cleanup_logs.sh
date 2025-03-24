#!/bin/bash

# 로그 파일 경로
LOG_DIR="/root/kospi-kosdaq-stock-analyzer"
LOG_PATTERN="ai_bot_*.log*"
DAYS_TO_KEEP=7

# 스크립트 실행 시간 기록
echo "$(date): 로그 정리 시작" >> "$LOG_DIR/log_cleanup.log"

# 7일 이상 된 로그 파일 삭제
find "$LOG_DIR" -name "$LOG_PATTERN" -type f -mtime +$DAYS_TO_KEEP -exec rm {} \;

# 삭제 후 남은 로그 파일 수 확인 및 기록
REMAINING_FILES=$(find "$LOG_DIR" -name "$LOG_PATTERN" | wc -l)
echo "$(date): 로그 정리 완료, 남은 로그 파일: $REMAINING_FILES" >> "$LOG_DIR/log_cleanup.log"