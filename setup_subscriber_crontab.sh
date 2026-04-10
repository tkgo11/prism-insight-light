#!/bin/bash

# =============================================================================
# PRISM-INSIGHT subscriber.py 전용 crontab 설정 스크립트
# =============================================================================
# 목적:
# - subscriber.py 를 실제 매매 시간에만 실행
# - KR/US 장 시작 시 자동 시작, 장 종료 직후 자동 중지
# - US 시장은 EST/EDT 전환을 재설치 없이 흡수
#
# 전제:
# - cron 시스템 타임존은 Asia/Seoul(KST) 여야 합니다.
# - KST가 아니면 설치 시 사용자에게 허락을 받고 Asia/Seoul로 변경합니다.
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
PID_FILE="${PID_FILE:-$PROJECT_DIR/runtime/subscriber.pid}"
USER_HOME="${HOME:-/home/$(whoami)}"
AUTO_SHUTDOWN="${AUTO_SHUTDOWN:-false}"
SHUTDOWN_COMMAND="${SHUTDOWN_COMMAND:-sudo shutdown -h now}"

BEGIN_MARKER="# BEGIN PRISM-INSIGHT SUBSCRIBER CRON"
END_MARKER="# END PRISM-INSIGHT SUBSCRIBER CRON"

detect_python_path() {
    if command -v pyenv >/dev/null 2>&1 && [ -d "$HOME/.pyenv" ]; then
        echo "$HOME/.pyenv/shims/python"
    elif [ -f "$PROJECT_DIR/venv/bin/python" ]; then
        echo "$PROJECT_DIR/venv/bin/python"
    elif [ -f "$PROJECT_DIR/.venv/bin/python" ]; then
        echo "$PROJECT_DIR/.venv/bin/python"
    else
        command -v python3 || command -v python
    fi
}

PYTHON_PATH="${PYTHON_PATH:-$(detect_python_path)}"

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

log_success() {
    echo -e "${GREEN}✓${NC} $1"
}

normalize_bool() {
    case "${1,,}" in
        1|true|yes|y|on|enable|enabled)
            echo "true"
            ;;
        *)
            echo "false"
            ;;
    esac
}

generate_path() {
    local paths=()

    if [ -d "$USER_HOME/.pyenv" ]; then
        paths+=("$USER_HOME/.pyenv/plugins/pyenv-virtualenv/shims")
        paths+=("$USER_HOME/.pyenv/shims")
        paths+=("$USER_HOME/.pyenv/bin")
    fi

    paths+=("/usr/local/sbin")
    paths+=("/usr/local/bin")
    paths+=("/usr/sbin")
    paths+=("/usr/bin")
    paths+=("/sbin")
    paths+=("/bin")

    if [ -d "$USER_HOME/.local/bin" ]; then
        paths+=("$USER_HOME/.local/bin")
    fi

    if [ -d "$USER_HOME/.cargo/bin" ]; then
        paths+=("$USER_HOME/.cargo/bin")
    fi

    local IFS=':'
    echo "${paths[*]}"
}

validate_environment() {
    AUTO_SHUTDOWN="$(normalize_bool "$AUTO_SHUTDOWN")"

    if [ ! -d "$PROJECT_DIR" ]; then
        log_error "프로젝트 디렉토리를 찾을 수 없습니다: $PROJECT_DIR"
        exit 1
    fi

    if [ ! -f "$PYTHON_PATH" ]; then
        log_error "Python 실행 파일을 찾을 수 없습니다: $PYTHON_PATH"
        exit 1
    fi

    if [ ! -f "$PROJECT_DIR/subscriber.py" ]; then
        log_error "subscriber.py 를 찾을 수 없습니다: $PROJECT_DIR/subscriber.py"
        exit 1
    fi

    if [ "$AUTO_SHUTDOWN" = "true" ] && [ -z "${SHUTDOWN_COMMAND// }" ]; then
        log_error "AUTO_SHUTDOWN=true 인 경우 SHUTDOWN_COMMAND 가 비어 있을 수 없습니다."
        exit 1
    fi

    mkdir -p "$LOG_DIR"
    mkdir -p "$(dirname "$PID_FILE")"
}

current_timezone() {
    if command -v timedatectl >/dev/null 2>&1; then
        timedatectl show -p Timezone --value 2>/dev/null && return 0
    fi

    if [ -f /etc/timezone ]; then
        cat /etc/timezone
        return 0
    fi

    date +%Z
}

ensure_kst_timezone() {
    local tz
    tz="$(current_timezone | tr -d '\r')"

    if [ "$tz" = "Asia/Seoul" ]; then
        return 0
    fi

    echo
    log_warn "현재 시스템 타임존은 '$tz' 입니다."
    log_warn "이 스크립트는 cron 기준 시간을 KST(Asia/Seoul)로 가정합니다."
    read -r -p "시스템 타임존을 Asia/Seoul로 변경할까요? (y/N): " confirm

    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        log_error "KST가 아니면 스케줄이 어긋날 수 있어 설치를 중단합니다."
        exit 1
    fi

    if command -v timedatectl >/dev/null 2>&1; then
        if [ "$(id -u)" -eq 0 ]; then
            timedatectl set-timezone Asia/Seoul
        elif command -v sudo >/dev/null 2>&1; then
            sudo timedatectl set-timezone Asia/Seoul
        else
            log_error "timedatectl 실행 권한이 없습니다. 수동으로 Asia/Seoul로 변경해주세요."
            exit 1
        fi
    elif [ "$(id -u)" -eq 0 ]; then
        ln -sf /usr/share/zoneinfo/Asia/Seoul /etc/localtime
        echo "Asia/Seoul" > /etc/timezone
    else
        log_error "자동 타임존 변경을 지원하지 않는 환경입니다. 수동으로 Asia/Seoul로 바꿔주세요."
        exit 1
    fi

    log_success "시스템 타임존을 Asia/Seoul로 변경했습니다."
}

market_open_now() {
    local market="$1"
    PROJECT_DIR="$PROJECT_DIR" "$PYTHON_PATH" - "$market" <<'PY'
import importlib.util
import os
import sys
from pathlib import Path

try:
    project_dir = Path(os.environ["PROJECT_DIR"])
    module_path = project_dir / "trading" / "market_hours.py"
    spec = importlib.util.spec_from_file_location("prism_market_hours", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    market = sys.argv[1]
    sys.exit(0 if module.is_market_open(market) else 1)
except Exception as exc:  # noqa: BLE001
    print(f"market-hours check failed: {exc}", file=sys.stderr)
    sys.exit(2)
PY
}

market_open_label() {
    local market="$1"
    if market_open_now "$market" >/dev/null 2>&1; then
        echo "yes"
        return 0
    fi

    case $? in
        1) echo "no" ;;
        *) echo "unknown" ;;
    esac
}

any_market_open_now() {
    local kr_status
    local us_status

    market_open_now KR >/dev/null 2>&1
    kr_status=$?

    market_open_now US >/dev/null 2>&1
    us_status=$?

    if [ "$kr_status" -eq 0 ] || [ "$us_status" -eq 0 ]; then
        return 0
    fi

    if [ "$kr_status" -eq 1 ] && [ "$us_status" -eq 1 ]; then
        return 1
    fi

    return 2
}

maybe_shutdown_system() {
    local market="$1"
    local market_status

    if [ "$AUTO_SHUTDOWN" != "true" ]; then
        return 0
    fi

    if process_running; then
        log_info "subscriber.py 가 아직 실행 중이라 시스템 종료를 건너뜁니다."
        return 0
    fi

    if market_open_now "$market" >/dev/null 2>&1; then
        log_info "$market 시장이 아직 열려 있어 시스템 종료를 건너뜁니다."
        return 0
    fi

    market_status=$?
    if [ "$market_status" -ne 1 ]; then
        log_error "$market 시장 상태 확인 실패로 시스템 종료를 건너뜁니다."
        return 1
    fi

    if any_market_open_now >/dev/null 2>&1; then
        log_info "다른 시장이 열려 있어 시스템 종료를 건너뜁니다."
        return 0
    fi

    market_status=$?
    if [ "$market_status" -ne 1 ]; then
        log_error "전체 시장 상태 확인 실패로 시스템 종료를 건너뜁니다."
        return 1
    fi

    log_warn "자동 시스템 종료를 실행합니다: $SHUTDOWN_COMMAND"
    bash -lc "$SHUTDOWN_COMMAND"
}

process_running() {
    [ -f "$PID_FILE" ] || return 1

    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    [ -n "$pid" ] || return 1

    kill -0 "$pid" 2>/dev/null
}

cleanup_stale_pid() {
    if [ -f "$PID_FILE" ] && ! process_running; then
        rm -f "$PID_FILE"
    fi
}

start_subscriber() {
    local market="$1"
    local market_status

    cleanup_stale_pid

    if market_open_now "$market" >/dev/null 2>&1; then
        market_status=0
    else
        market_status=$?
        if [ "$market_status" -eq 1 ]; then
            log_info "$market 시장이 현재 열려 있지 않아 subscriber.py 시작을 건너뜁니다."
            return 0
        fi

        log_error "$market 시장 상태 확인에 실패했습니다. Python 의존성 및 설정을 확인해주세요."
        return 1
    fi

    if process_running; then
        log_info "subscriber.py 가 이미 실행 중입니다."
        return 0
    fi

    nohup "$PYTHON_PATH" "$PROJECT_DIR/subscriber.py" \
        --log-file "$LOG_DIR/subscriber.log" \
        >> "$LOG_DIR/subscriber_runtime.log" 2>&1 < /dev/null &

    echo $! > "$PID_FILE"
    log_success "subscriber.py 를 시작했습니다. market=$market pid=$(cat "$PID_FILE")"
}

stop_subscriber() {
    local market="$1"
    local market_status

    cleanup_stale_pid

    if ! process_running; then
        log_info "중지할 subscriber.py 프로세스가 없습니다."
        return 0
    fi

    if market_open_now "$market" >/dev/null 2>&1; then
        log_info "$market 시장이 아직 열려 있어 subscriber.py 중지를 건너뜁니다."
        return 0
    fi

    market_status=$?
    if [ "$market_status" -ne 1 ]; then
        log_error "$market 시장 상태 확인에 실패했습니다. 안전을 위해 subscriber.py 중지를 보류합니다."
        return 1
    fi

    local pid
    pid="$(cat "$PID_FILE")"

    kill "$pid" 2>/dev/null || true

    for _ in 1 2 3 4 5; do
        if ! kill -0 "$pid" 2>/dev/null; then
            rm -f "$PID_FILE"
            log_success "subscriber.py 를 중지했습니다. pid=$pid"
            return 0
        fi
        sleep 1
    done

    kill -9 "$pid" 2>/dev/null || true
    rm -f "$PID_FILE"
    log_warn "subscriber.py 가 정상 종료되지 않아 강제 종료했습니다. pid=$pid"
}

show_status() {
    cleanup_stale_pid

    echo "Timezone: $(current_timezone | tr -d '\r')"
    echo "Project : $PROJECT_DIR"
    echo "Python  : $PYTHON_PATH"
    echo "PID file: $PID_FILE"
    echo "KR open : $(market_open_label KR)"
    echo "US open : $(market_open_label US)"

    if process_running; then
        echo "Status  : running (pid $(cat "$PID_FILE"))"
    else
        echo "Status  : stopped"
    fi
    echo "Shutdown: $AUTO_SHUTDOWN"
    echo "Command : $SHUTDOWN_COMMAND"
}

print_schedule_summary() {
    cat <<EOF
subscriber.py 실제 매매 시간 기준 운용 요약

[실제 시장 시간]
- KR: 영업일 09:00~15:30 KST
- US: NYSE 영업일 09:30~16:00 America/New_York
  - EDT 기간 KST 환산: 22:30~다음날 05:00
  - EST 기간 KST 환산: 23:30~다음날 06:00

[cron 트리거 방식]
- KR 시작: 월~금 09:00 KST
- KR 중지: 월~금 15:31 KST
- US 시작: 월~금 22:30 KST, 월~금 23:30 KST
- US 중지: 화~토 05:01 KST, 화~토 06:01 KST

[선택 옵션]
- AUTO_SHUTDOWN=true 이면 장 종료 후 subscriber.py 가 중지된 시점에 시스템 종료를 시도합니다.
- 종료 전 KR/US 둘 다 닫혀 있는지 다시 확인합니다.
- 종료 명령은 SHUTDOWN_COMMAND 로 바꿀 수 있습니다.

설명:
- US는 서머타임(EDT)/표준시(EST) 전환 때문에 시작/중지 후보 시간을 둘 다 등록합니다.
- 실제 시작/중지는 실행 시점에 trading.market_hours 로 시장 개장 여부를 다시 확인해서 결정합니다.
- 휴장일에는 cron이 돌더라도 subscriber.py 는 시작되지 않습니다.
EOF
}

generate_managed_block() {
    local shutdown_command_quoted
    shutdown_command_quoted="$(printf '%q' "$SHUTDOWN_COMMAND")"

    cat <<EOF
$BEGIN_MARKER
# Generated by setup_subscriber_crontab.sh on $(date)
# NOTE: This schedule assumes system timezone is Asia/Seoul.
SHELL=/bin/bash
PATH=$(generate_path)
PYTHONPATH=$PROJECT_DIR
AUTO_SHUTDOWN=$AUTO_SHUTDOWN

# KR market session
0 9 * * 1-5 cd "$PROJECT_DIR" && SHUTDOWN_COMMAND=$shutdown_command_quoted bash "$SCRIPT_PATH" --cron-start KR
31 15 * * 1-5 cd "$PROJECT_DIR" && SHUTDOWN_COMMAND=$shutdown_command_quoted bash "$SCRIPT_PATH" --cron-stop KR

# US market session
30 22 * * 1-5 cd "$PROJECT_DIR" && SHUTDOWN_COMMAND=$shutdown_command_quoted bash "$SCRIPT_PATH" --cron-start US
30 23 * * 1-5 cd "$PROJECT_DIR" && SHUTDOWN_COMMAND=$shutdown_command_quoted bash "$SCRIPT_PATH" --cron-start US
1 5 * * 2-6 cd "$PROJECT_DIR" && SHUTDOWN_COMMAND=$shutdown_command_quoted bash "$SCRIPT_PATH" --cron-stop US
1 6 * * 2-6 cd "$PROJECT_DIR" && SHUTDOWN_COMMAND=$shutdown_command_quoted bash "$SCRIPT_PATH" --cron-stop US
$END_MARKER
EOF
}

strip_managed_block() {
    local input_file="$1"
    local output_file="$2"

    awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
        $0 == begin { skip = 1; next }
        $0 == end { skip = 0; next }
        !skip { print }
    ' "$input_file" > "$output_file"
}

backup_crontab() {
    local backup_file="$PROJECT_DIR/crontab_subscriber_backup_$(date +%Y%m%d_%H%M%S).txt"

    if crontab -l >/dev/null 2>&1; then
        crontab -l > "$backup_file"
        log_success "현재 crontab을 백업했습니다: $backup_file"
    else
        log_info "기존 crontab이 없어 백업을 생략합니다."
    fi
}

install_crontab() {
    local temp_current
    local temp_clean
    temp_current="$(mktemp)"
    temp_clean="$(mktemp)"

    if crontab -l >/dev/null 2>&1; then
        crontab -l > "$temp_current"
    else
        : > "$temp_current"
    fi

    strip_managed_block "$temp_current" "$temp_clean"

    {
        cat "$temp_clean"
        [ -s "$temp_clean" ] && echo
        generate_managed_block
    } > "$temp_current"

    crontab "$temp_current"
    rm -f "$temp_current" "$temp_clean"
    log_success "subscriber.py 전용 crontab을 설치했습니다."
}

uninstall_crontab() {
    local temp_current
    local temp_clean
    temp_current="$(mktemp)"
    temp_clean="$(mktemp)"

    if ! crontab -l >/dev/null 2>&1; then
        log_info "제거할 crontab이 없습니다."
        rm -f "$temp_current" "$temp_clean"
        return 0
    fi

    crontab -l > "$temp_current"
    strip_managed_block "$temp_current" "$temp_clean"

    if [ -s "$temp_clean" ]; then
        crontab "$temp_clean"
    else
        crontab -r
    fi

    rm -f "$temp_current" "$temp_clean"
    log_success "subscriber.py 전용 crontab을 제거했습니다."
}

show_crontab() {
    if ! crontab -l >/dev/null 2>&1; then
        log_warn "현재 crontab이 없습니다."
        return 0
    fi

    crontab -l | awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
        $0 == begin { show = 1 }
        show { print }
        $0 == end { exit }
    '
}

interactive_action_menu() {
    echo -e "${BLUE}================================================${NC}"
    echo -e "${BLUE}   subscriber.py 전용 Crontab 메뉴${NC}"
    echo -e "${BLUE}================================================${NC}"
    echo
    echo "실행할 작업을 선택하세요:"
    echo "  1) 설치"
    echo "  2) 제거"
    echo "  3) 현재 설정 보기"
    echo "  4) 현재 crontab 백업"
    echo "  5) 스케줄 요약 보기"
    echo "  6) subscriber 상태 보기"
    echo

    read -r -p "선택 [1]: " action_choice
    case "${action_choice:-1}" in
        1) INTERACTIVE_ACTION="install" ;;
        2) INTERACTIVE_ACTION="uninstall" ;;
        3) INTERACTIVE_ACTION="show" ;;
        4) INTERACTIVE_ACTION="backup" ;;
        5) INTERACTIVE_ACTION="summary" ;;
        6) INTERACTIVE_ACTION="status" ;;
        *)
            log_warn "알 수 없는 선택이라 설치 모드로 진행합니다."
            INTERACTIVE_ACTION="install"
            ;;
    esac
}

interactive_common_settings() {
    echo
    read -r -p "프로젝트 경로 [$PROJECT_DIR]: " input_dir
    PROJECT_DIR="${input_dir:-$PROJECT_DIR}"

    read -r -p "Python 실행 파일 경로 [$PYTHON_PATH]: " input_python
    PYTHON_PATH="${input_python:-$PYTHON_PATH}"

    read -r -p "로그 디렉토리 [$LOG_DIR]: " input_log
    LOG_DIR="${input_log:-$LOG_DIR}"

    read -r -p "PID 파일 경로 [$PID_FILE]: " input_pid
    PID_FILE="${input_pid:-$PID_FILE}"
}

interactive_setup() {
    echo -e "${BLUE}================================================${NC}"
    echo -e "${BLUE}   subscriber.py 전용 Crontab 설정 도구${NC}"
    echo -e "${BLUE}================================================${NC}"
    interactive_common_settings

    read -r -p "자동 시스템 종료 사용 여부 [y/N]: " input_shutdown
    AUTO_SHUTDOWN="$(normalize_bool "${input_shutdown:-false}")"

    if [ "$AUTO_SHUTDOWN" = "true" ]; then
        read -r -p "종료 명령 [$SHUTDOWN_COMMAND]: " input_shutdown_cmd
        SHUTDOWN_COMMAND="${input_shutdown_cmd:-$SHUTDOWN_COMMAND}"
    fi

    validate_environment
    print_schedule_summary
    echo
    show_status
    echo

    read -r -p "계속하시겠습니까? (y/N): " confirm
    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        log_info "취소되었습니다."
        exit 0
    fi
}

show_help() {
    cat <<EOF
사용법: $0 [옵션]

옵션:
  -h, --help          도움말 표시
  -i, --install       subscriber 전용 crontab 설치 (기본값)
  -u, --uninstall     subscriber 전용 crontab 제거
  -s, --show          현재 설치된 관리 블록 표시
  -b, --backup        현재 crontab 백업
  --summary           실제 매매 시간 및 cron 스케줄 요약 출력
  --status            현재 subscriber 상태 출력
  --enable-auto-shutdown
                     장 종료 후 조건 충족 시 시스템 종료 활성화
  --disable-auto-shutdown
                     자동 시스템 종료 비활성화
  --shutdown-command CMD
                     자동 종료 시 사용할 명령 지정
  --non-interactive   대화형 입력 건너뛰기

내부용 옵션:
  --cron-start KR|US
  --cron-stop KR|US

환경 변수:
  PROJECT_DIR   프로젝트 경로 (기본값: 스크립트 위치)
  PYTHON_PATH   Python 실행 파일 경로 (기본값: 자동 감지)
  LOG_DIR       로그 디렉토리 (기본값: \$PROJECT_DIR/logs)
  PID_FILE      subscriber PID 파일 경로
  AUTO_SHUTDOWN true | false
  SHUTDOWN_COMMAND 종료 명령

예시:
  $0
  $0 --summary
  $0 --install --non-interactive
  PROJECT_DIR=/opt/prism-insight-light $0 --status
EOF
}

main() {
    local action="install"
    local interactive=true
    local cron_market=""
    local action_explicit=false
    INTERACTIVE_ACTION=""

    while [[ $# -gt 0 ]]; do
        case "$1" in
            -h|--help)
                show_help
                exit 0
                ;;
            -i|--install)
                action="install"
                action_explicit=true
                ;;
            -u|--uninstall)
                action="uninstall"
                action_explicit=true
                ;;
            -s|--show)
                action="show"
                action_explicit=true
                ;;
            -b|--backup)
                action="backup"
                action_explicit=true
                ;;
            --summary)
                action="summary"
                action_explicit=true
                ;;
            --status)
                action="status"
                action_explicit=true
                ;;
            --enable-auto-shutdown)
                AUTO_SHUTDOWN="true"
                ;;
            --disable-auto-shutdown)
                AUTO_SHUTDOWN="false"
                ;;
            --shutdown-command)
                shift
                SHUTDOWN_COMMAND="${1:-}"
                ;;
            --cron-start)
                action="cron-start"
                shift
                cron_market="${1:-}"
                ;;
            --cron-stop)
                action="cron-stop"
                shift
                cron_market="${1:-}"
                ;;
            --non-interactive)
                interactive=false
                ;;
            *)
                log_error "알 수 없는 옵션입니다: $1"
                show_help
                exit 1
                ;;
        esac
        shift
    done

    if $interactive && ! $action_explicit; then
        interactive_action_menu
        action="${INTERACTIVE_ACTION:-install}"
    fi

    case "$action" in
        install)
            if $interactive; then
                interactive_setup
                ensure_kst_timezone
            else
                validate_environment
                ensure_kst_timezone
            fi
            backup_crontab
            install_crontab
            echo
            print_schedule_summary
            ;;
        uninstall)
            if $interactive; then
                interactive_common_settings
            fi
            validate_environment
            if $interactive; then
                read -r -p "subscriber 전용 crontab을 제거할까요? (y/N): " confirm
                if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
                    log_info "취소되었습니다."
                    exit 0
                fi
            fi
            backup_crontab
            uninstall_crontab
            ;;
        show)
            if $interactive; then
                interactive_common_settings
            fi
            validate_environment
            show_crontab
            ;;
        backup)
            if $interactive; then
                interactive_common_settings
            fi
            validate_environment
            backup_crontab
            ;;
        summary)
            if $interactive; then
                interactive_common_settings
            fi
            validate_environment
            print_schedule_summary
            ;;
        status)
            if $interactive; then
                interactive_common_settings
            fi
            validate_environment
            show_status
            ;;
        cron-start)
            validate_environment
            if [ "$cron_market" != "KR" ] && [ "$cron_market" != "US" ]; then
                log_error "--cron-start 뒤에는 KR 또는 US 가 필요합니다."
                exit 1
            fi
            start_subscriber "$cron_market"
            ;;
        cron-stop)
            validate_environment
            if [ "$cron_market" != "KR" ] && [ "$cron_market" != "US" ]; then
                log_error "--cron-stop 뒤에는 KR 또는 US 가 필요합니다."
                exit 1
            fi
            stop_subscriber "$cron_market"
            maybe_shutdown_system "$cron_market"
            ;;
    esac
}

main "$@"
