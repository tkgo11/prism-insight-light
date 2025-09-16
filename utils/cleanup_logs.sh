#!/bin/bash

# 로그 파일 경로
LOG_DIR="/root/prism-insight"
DAYS_TO_KEEP=7

# 스크립트 실행 시간 기록
echo "$(date): 로그 정리 시작" >> "$LOG_DIR/utils/log_cleanup.log"

# 삭제할 로그 파일 패턴 목록
LOG_PATTERNS=(
    "ai_bot_*.log*"
    "trigger_results_morning_*.json"
    "trigger_results_afternoon_*.json"
    "stock_tracking_*.log"
    "orchestrator_*.log"
)

# 7일 이상 된 로그 파일 삭제
for PATTERN in "${LOG_PATTERNS[@]}"; do
    find "$LOG_DIR" -name "$PATTERN" -type f -mtime +$DAYS_TO_KEEP -exec rm {} \;
done

# logs 디렉토리 내의 누적 로그파일 처리 (내용 비우기) - 일주일에 한 번만 실행
LOGS_DIR="$LOG_DIR/logs"
if [ -d "$LOGS_DIR" ] && [ $(date +%u) -eq 7 ]; then  # 일요일(7)에만 실행
    LOG_ACCUMULATING_PATTERN="stock_analysis_*.log"
    find "$LOGS_DIR" -name "$LOG_ACCUMULATING_PATTERN" -type f -exec sh -c '> {}' \;
    echo "$(date): logs 디렉토리의 누적 로그파일 내용을 비웠습니다." >> "$LOG_DIR/utils/log_cleanup.log"
fi

# 삭제 후 남은 로그 파일 수 확인 및 기록
REMAINING_FILES=0
for PATTERN in "${LOG_PATTERNS[@]}"; do
    COUNT=$(find "$LOG_DIR" -name "$PATTERN" | wc -l)
    REMAINING_FILES=$((REMAINING_FILES + COUNT))
done

echo "$(date): 로그 정리 완료, 남은 로그 파일: $REMAINING_FILES" >> "$LOG_DIR/utils/log_cleanup.log"