#!/bin/bash

# =============================================================================
# PRISM-INSIGHT 간편 Crontab 설정 스크립트
# =============================================================================
# 최소한의 설정으로 빠르게 crontab을 구성하는 스크립트입니다.
# =============================================================================

# 색상 정의
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo "🚀 PRISM-INSIGHT Crontab 빠른 설정"
echo "=================================="

# 현재 디렉토리를 프로젝트 경로로 설정
PROJECT_DIR=$(pwd)

# Python 경로 자동 감지
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
else
    echo -e "${RED}❌ Python을 찾을 수 없습니다. Python을 먼저 설치해주세요.${NC}"
    exit 1
fi

# 로그 디렉토리 생성
mkdir -p "$PROJECT_DIR/logs"

# 임시 crontab 파일 생성
TEMP_CRON="/tmp/prism_cron_$$"

# 기존 crontab 백업
if crontab -l &> /dev/null; then
    echo "📦 기존 crontab 백업 중..."
    crontab -l > "$PROJECT_DIR/crontab_backup_$(date +%Y%m%d).txt"
    crontab -l > "$TEMP_CRON"
else
    touch "$TEMP_CRON"
fi

# PRISM-INSIGHT 스케줄 추가
cat >> "$TEMP_CRON" << EOF

# === PRISM-INSIGHT 자동 실행 스케줄 ===
# 오전 9시 30분 - 오전 분석 (월-금)
30 9 * * 1-5 cd $PROJECT_DIR && $PYTHON_CMD stock_analysis_orchestrator.py --mode morning >> $PROJECT_DIR/logs/morning.log 2>&1

# 오후 3시 40분 - 오후 분석 (월-금)
40 15 * * 1-5 cd $PROJECT_DIR && $PYTHON_CMD stock_analysis_orchestrator.py --mode afternoon >> $PROJECT_DIR/logs/afternoon.log 2>&1

# 오전 7시 - 데이터 업데이트 (월-금)
0 7 * * 1-5 cd $PROJECT_DIR && $PYTHON_CMD update_stock_data.py >> $PROJECT_DIR/logs/update.log 2>&1

# 오전 3시 - 로그 정리
0 3 * * * cd $PROJECT_DIR && bash utils/cleanup_logs.sh 2>&1
EOF

# Crontab 설치
crontab "$TEMP_CRON"
rm -f "$TEMP_CRON"

echo -e "${GREEN}✅ Crontab 설정 완료!${NC}"
echo ""
echo "📋 설정된 스케줄:"
echo "  • 오전 9:30 - 장 시작 분석"
echo "  • 오후 3:40 - 장 마감 분석"
echo "  • 오전 7:00 - 데이터 업데이트"
echo "  • 오전 3:00 - 로그 정리"
echo ""
echo "💡 확인: crontab -l"
echo "💡 제거: crontab -r"
