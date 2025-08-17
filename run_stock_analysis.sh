#!/bin/bash

# .pyenv 환경 활성화 (스크립트 시작 부분에 추가 - 반드시 추가!)
PYENV_ROOT="$HOME/.pyenv"
export PYENV_ROOT
export PATH="$PYENV_ROOT/bin:$PATH"
eval "$(pyenv init -)"

# 로그 파일 설정
LOG_FILE="/root/prism-insight/stock_scheduler.log"
SCRIPT_DIR="/root/prism-insight"

# 로그 함수
log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" >> $LOG_FILE
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1"
}

# 현재 디렉토리를 스크립트 디렉토리로 변경
cd $SCRIPT_DIR

# 시장 영업일 체크
log "주식 시장 영업일 체크 시작"
/root/.pyenv/shims/python check_market_day.py
MARKET_CHECK=$?

if [ $MARKET_CHECK -ne 0 ]; then
    log "오늘은 주식 시장 영업일이 아닙니다. 스크립트 실행을 건너뜁니다."
    exit 0
fi

# 실행할 프로그램 모드
MODE=$1
ACCOUNT_TYPE="premium"
TODAY=$(date +%Y%m%d)

# 로그 파일 지정 (날짜별)
BATCH_LOG_FILE="${SCRIPT_DIR}/logs/stock_analysis_${MODE}_${TODAY}.log"
mkdir -p "${SCRIPT_DIR}/logs"

# 로그 출력
log "실행 모드: $MODE, 로그 파일: $BATCH_LOG_FILE"

# 가상환경 활성화 (있는 경우)
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

# 백그라운드에서 스크립트 실행
log "$MODE 배치 백그라운드 실행 시작"
nohup /root/.pyenv/shims/python stock_analysis_orchestrator.py --mode $MODE --account-type $ACCOUNT_TYPE > $BATCH_LOG_FILE 2>&1 &

# 실행된 프로세스 ID 저장
PID=$!
log "프로세스 ID: $PID 로 실행됨"

# PID 파일 생성 (나중에 상태 확인 용도)
echo $PID > "${SCRIPT_DIR}/logs/stock_analysis_${MODE}_${TODAY}.pid"

# 가상환경 비활성화 (활성화한 경우)
if [ -f "venv/bin/activate" ]; then
    deactivate
fi

log "$MODE 배치 실행 요청 완료"
exit 0