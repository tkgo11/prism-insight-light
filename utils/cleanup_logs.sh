#!/bin/bash

# 프로젝트 루트 디렉토리 자동 감지
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"  # utils의 부모 디렉토리

# 로그 파일 경로
LOG_DIR="$PROJECT_ROOT"
DAYS_TO_KEEP=7

# utils 디렉토리 생성 (없는 경우)
mkdir -p "$PROJECT_ROOT/utils"

# 스크립트 실행 시간 기록
echo "$(date): 로그 정리 시작" >> "$PROJECT_ROOT/utils/log_cleanup.log"

# 삭제할 로그 파일 패턴 목록
LOG_PATTERNS=(
    "ai_bot_*.log*"
    "trigger_results_morning_*.json"
    "trigger_results_afternoon_*.json"
    "*stock_tracking_*.log"
    "orchestrator_*.log"
)

# 7일 이상 된 로그 파일 삭제
for PATTERN in "${LOG_PATTERNS[@]}"; do
    find "$LOG_DIR" -name "$PATTERN" -type f -mtime +$DAYS_TO_KEEP -exec rm {} \;
done

# logs 디렉토리 내의 누적 로그파일 처리 (내용 비우기) - 일주일에 한 번만 실행
LOGS_DIR="$PROJECT_ROOT/logs"
if [ -d "$LOGS_DIR" ] && [ $(date +%u) -eq 7 ]; then  # 일요일(7)에만 실행
    LOG_ACCUMULATING_PATTERN="stock_analysis_*.log"
    find "$LOGS_DIR" -name "$LOG_ACCUMULATING_PATTERN" -type f -exec sh -c '> {}' \;
    echo "$(date): logs 디렉토리의 누적 로그파일 내용을 비웠습니다." >> "$PROJECT_ROOT/utils/log_cleanup.log"
fi

# 삭제 후 남은 로그 파일 수 확인 및 기록
REMAINING_FILES=0
for PATTERN in "${LOG_PATTERNS[@]}"; do
    COUNT=$(find "$LOG_DIR" -name "$PATTERN" 2>/dev/null | wc -l)
    REMAINING_FILES=$((REMAINING_FILES + COUNT))
done

echo "$(date): 로그 정리 완료, 남은 로그 파일: $REMAINING_FILES" >> "$PROJECT_ROOT/utils/log_cleanup.log"