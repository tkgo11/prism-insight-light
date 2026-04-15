#!/bin/bash

# =============================================================================
# PRISM-INSIGHT subscriber Docker 전용 crontab 설정 스크립트
# =============================================================================
# 목적:
# - subscriber 컨테이너를 실제 매매 시간에만 실행
# - 설치 시 컨테이너를 1회 생성하고, KR/US 장 시작 시 docker start, 장 종료 직후 docker stop
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
RUNTIME_DIR="${RUNTIME_DIR:-$PROJECT_DIR/runtime}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
CRONTAB_BIN="${CRONTAB_BIN:-crontab}"
TIMEDATECTL_BIN="${TIMEDATECTL_BIN:-timedatectl}"
SUDO_BIN="${SUDO_BIN:-sudo}"
IMAGE_NAME="${IMAGE_NAME:-pubsub-trader}"
CONTAINER_NAME="${CONTAINER_NAME:-prism-insight-subscriber}"
ENV_FILE="${ENV_FILE:-$PROJECT_DIR/.env}"
KIS_CONFIG_HOST_PATH="${KIS_CONFIG_HOST_PATH:-$PROJECT_DIR/trading/config/kis_devlp.yaml}"
KIS_CONFIG_CONTAINER_PATH="${KIS_CONFIG_CONTAINER_PATH:-/app/trading/config/kis_devlp.yaml}"
CREDENTIALS_HOST_PATH="${CREDENTIALS_HOST_PATH:-}"
CREDENTIALS_CONTAINER_PATH="${CREDENTIALS_CONTAINER_PATH:-/app/runtime/gcp-credentials.json}"
AUTO_BUILD_IMAGE="${AUTO_BUILD_IMAGE:-true}"
AUTO_SHUTDOWN="${AUTO_SHUTDOWN:-false}"
SHUTDOWN_COMMAND="${SHUTDOWN_COMMAND:-sudo shutdown -h now}"
USER_HOME="${HOME:-/home/$(whoami)}"

BEGIN_MARKER="# BEGIN PRISM-INSIGHT SUBSCRIBER DOCKER CRON"
END_MARKER="# END PRISM-INSIGHT SUBSCRIBER DOCKER CRON"
INTERACTIVE_ACTION=""

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

shell_quote() {
    printf '%q' "$1"
}

sanitize_input() {
    printf '%s' "${1//$'\r'/}"
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

current_timezone() {
    if command -v "$TIMEDATECTL_BIN" >/dev/null 2>&1; then
        "$TIMEDATECTL_BIN" show -p Timezone --value 2>/dev/null && return 0
    fi

    if [ -f /etc/timezone ]; then
        cat /etc/timezone
        return 0
    fi

    date +%Z
}

is_kst_timezone() {
    case "$1" in
        Asia/Seoul|KST)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

ensure_kst_timezone() {
    local tz
    local auto_confirm
    tz="$(current_timezone | tr -d '\r')"

    if is_kst_timezone "$tz"; then
        return 0
    fi

    echo
    log_warn "현재 시스템 타임존은 '$tz' 입니다."
    log_warn "이 스크립트는 cron 기준 시간을 KST(Asia/Seoul)로 가정합니다."

    auto_confirm="$(normalize_bool "${AUTO_CONFIRM_TIMEZONE_CHANGE:-false}")"
    if [ "$auto_confirm" = "true" ]; then
        confirm="y"
        log_info "AUTO_CONFIRM_TIMEZONE_CHANGE=true 로 타임존 변경을 자동 승인합니다."
    else
        read -r -p "시스템 타임존을 Asia/Seoul로 변경할까요? (y/N): " confirm
    fi

    if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
        log_error "KST가 아니면 스케줄이 어긋날 수 있어 설치를 중단합니다."
        exit 1
    fi

    if command -v "$TIMEDATECTL_BIN" >/dev/null 2>&1; then
        if [ "$(id -u)" -eq 0 ]; then
            "$TIMEDATECTL_BIN" set-timezone Asia/Seoul
        elif command -v "$SUDO_BIN" >/dev/null 2>&1; then
            "$SUDO_BIN" "$TIMEDATECTL_BIN" set-timezone Asia/Seoul
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

read_env_value() {
    local key="$1"

    [ -f "$ENV_FILE" ] || return 1

    awk -F= -v key="$key" '
        $1 == key {
            val = substr($0, index($0, "=") + 1)
            gsub(/^[ \t]+|[ \t]+$/, "", val)
            if ((val ~ /^".*"$/) || (val ~ /^'\''.*'\''$/)) {
                val = substr(val, 2, length(val) - 2)
            }
            print val
            exit
        }
    ' "$ENV_FILE"
}

initialize_credentials_path() {
    if [ -n "$CREDENTIALS_HOST_PATH" ]; then
        return 0
    fi

    local inferred
    inferred="$(read_env_value "GCP_CREDENTIALS_PATH" || true)"
    if [ -n "$inferred" ]; then
        CREDENTIALS_HOST_PATH="$inferred"
    fi
}

validate_environment() {
    AUTO_BUILD_IMAGE="$(normalize_bool "$AUTO_BUILD_IMAGE")"
    AUTO_SHUTDOWN="$(normalize_bool "$AUTO_SHUTDOWN")"
    initialize_credentials_path

    if [ ! -d "$PROJECT_DIR" ]; then
        log_error "프로젝트 디렉토리를 찾을 수 없습니다: $PROJECT_DIR"
        exit 1
    fi

    if ! command -v "$DOCKER_BIN" >/dev/null 2>&1; then
        log_error "Docker 실행 파일을 찾을 수 없습니다: $DOCKER_BIN"
        exit 1
    fi

    if [ ! -f "$PROJECT_DIR/Dockerfile" ]; then
        log_error "Dockerfile 을 찾을 수 없습니다: $PROJECT_DIR/Dockerfile"
        exit 1
    fi

    if [ ! -f "$ENV_FILE" ]; then
        log_error ".env 파일을 찾을 수 없습니다: $ENV_FILE"
        exit 1
    fi

    if [ ! -f "$KIS_CONFIG_HOST_PATH" ]; then
        log_error "KIS 설정 파일을 찾을 수 없습니다: $KIS_CONFIG_HOST_PATH"
        exit 1
    fi

    if [ -n "$CREDENTIALS_HOST_PATH" ] && [ ! -f "$CREDENTIALS_HOST_PATH" ]; then
        log_error "GCP 자격증명 파일을 찾을 수 없습니다: $CREDENTIALS_HOST_PATH"
        exit 1
    fi

    if [ "$AUTO_SHUTDOWN" = "true" ] && [ -z "${SHUTDOWN_COMMAND// }" ]; then
        log_error "AUTO_SHUTDOWN=true 인 경우 SHUTDOWN_COMMAND 가 비어 있을 수 없습니다."
        exit 1
    fi

    mkdir -p "$LOG_DIR"
    mkdir -p "$RUNTIME_DIR"
}

image_exists() {
    "$DOCKER_BIN" image inspect "$IMAGE_NAME" >/dev/null 2>&1
}

container_exists() {
    [ -n "$("$DOCKER_BIN" ps -a --filter "name=^/${CONTAINER_NAME}$" --format '{{.Names}}')" ]
}

container_running() {
    [ -n "$("$DOCKER_BIN" ps --filter "name=^/${CONTAINER_NAME}$" --format '{{.Names}}')" ]
}

remove_container_if_exists() {
    if container_exists; then
        "$DOCKER_BIN" rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    fi
}

build_image() {
    log_info "Docker 이미지를 빌드합니다: $IMAGE_NAME"
    (cd "$PROJECT_DIR" && "$DOCKER_BIN" build -t "$IMAGE_NAME" .)
}

ensure_image_ready() {
    if image_exists; then
        return 0
    fi

    if [ "$AUTO_BUILD_IMAGE" = "true" ]; then
        build_image
        return 0
    fi

    log_error "Docker 이미지가 없습니다: $IMAGE_NAME"
    log_error "AUTO_BUILD_IMAGE=true 로 설정하거나 수동으로 이미지를 빌드해주세요."
    return 1
}

market_open_now() {
    local market="$1"

    ensure_image_ready >/dev/null 2>&1 || return 2

    "$DOCKER_BIN" run --rm --entrypoint python "$IMAGE_NAME" - "$market" <<'PY'
import importlib.util
import sys
from pathlib import Path

try:
    module_path = Path("/app/trading/market_hours.py")
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
    local status

    if [ "$AUTO_SHUTDOWN" != "true" ]; then
        return 0
    fi

    if container_running; then
        log_info "subscriber 컨테이너가 아직 실행 중이라 시스템 종료를 건너뜁니다."
        return 0
    fi

    if market_open_now "$market" >/dev/null 2>&1; then
        log_info "$market 시장이 아직 열려 있어 시스템 종료를 건너뜁니다."
        return 0
    fi

    status=$?
    if [ "$status" -ne 1 ]; then
        log_error "$market 시장 상태 확인 실패로 시스템 종료를 건너뜁니다."
        return 1
    fi

    if any_market_open_now >/dev/null 2>&1; then
        log_info "다른 시장이 열려 있어 시스템 종료를 건너뜁니다."
        return 0
    fi

    status=$?
    if [ "$status" -ne 1 ]; then
        log_error "전체 시장 상태 확인 실패로 시스템 종료를 건너뜁니다."
        return 1
    fi

    log_warn "자동 시스템 종료를 실행합니다: $SHUTDOWN_COMMAND"
    bash -lc "$SHUTDOWN_COMMAND"
}

docker_create_command() {
    local cmd=(
        "$DOCKER_BIN" create
        --name "$CONTAINER_NAME"
        --restart no
        --env-file "$ENV_FILE"
        -e TZ=Asia/Seoul
        -v "$LOG_DIR:/app/logs"
        -v "$RUNTIME_DIR:/app/runtime"
        -v "$KIS_CONFIG_HOST_PATH:$KIS_CONFIG_CONTAINER_PATH:ro"
    )

    if [ -n "$CREDENTIALS_HOST_PATH" ]; then
        cmd+=(
            -e "GCP_CREDENTIALS_PATH=$CREDENTIALS_CONTAINER_PATH"
            -v "$CREDENTIALS_HOST_PATH:$CREDENTIALS_CONTAINER_PATH:ro"
        )
    fi

    cmd+=("$IMAGE_NAME")
    printf '%s\0' "${cmd[@]}"
}

create_container_definition() {
    local -a cmd

    ensure_image_ready
    remove_container_if_exists

    mapfile -d '' -t cmd < <(docker_create_command)
    "${cmd[@]}" >/dev/null
    log_success "subscriber 컨테이너 정의를 생성했습니다. container=$CONTAINER_NAME"
}

prepare_runtime() {
    validate_environment
    create_container_definition
}

install_cron_schedule() {
    ensure_kst_timezone
    backup_crontab
    install_crontab
}

ensure_container_ready() {
    ensure_image_ready || return 1

    if container_exists; then
        return 0
    fi

    create_container_definition
}

start_container() {
    local market="$1"
    local status

    if market_open_now "$market" >/dev/null 2>&1; then
        status=0
    else
        status=$?
        if [ "$status" -eq 1 ]; then
            log_info "$market 시장이 현재 열려 있지 않아 컨테이너 시작을 건너뜁니다."
            return 0
        fi

        log_error "$market 시장 상태 확인에 실패했습니다. 이미지와 의존성을 확인해주세요."
        return 1
    fi

    if container_running; then
        log_info "subscriber 컨테이너가 이미 실행 중입니다."
        return 0
    fi

    ensure_container_ready

    "$DOCKER_BIN" start "$CONTAINER_NAME" >/dev/null
    log_success "subscriber 컨테이너를 시작했습니다. market=$market container=$CONTAINER_NAME"
}

stop_container() {
    local market="$1"
    local status

    if ! container_running; then
        log_info "중지할 subscriber 컨테이너가 없습니다."
        return 0
    fi

    if market_open_now "$market" >/dev/null 2>&1; then
        log_info "$market 시장이 아직 열려 있어 컨테이너 중지를 건너뜁니다."
        return 0
    fi

    status=$?
    if [ "$status" -ne 1 ]; then
        log_error "$market 시장 상태 확인에 실패했습니다. 안전을 위해 컨테이너 중지를 보류합니다."
        return 1
    fi

    "$DOCKER_BIN" stop "$CONTAINER_NAME" >/dev/null 2>&1 || true
    log_success "subscriber 컨테이너를 중지했습니다. container=$CONTAINER_NAME"
}

show_status() {
    echo "Timezone        : $(current_timezone | tr -d '\r')"
    echo "Project         : $PROJECT_DIR"
    echo "Docker          : $DOCKER_BIN"
    echo "Image           : $IMAGE_NAME"
    echo "Container       : $CONTAINER_NAME"
    echo "Env file        : $ENV_FILE"
    echo "KIS config      : $KIS_CONFIG_HOST_PATH -> $KIS_CONFIG_CONTAINER_PATH"
    echo "Credentials     : ${CREDENTIALS_HOST_PATH:-<none>} -> $CREDENTIALS_CONTAINER_PATH"
    echo "Logs            : $LOG_DIR"
    echo "Runtime         : $RUNTIME_DIR"
    echo "Image exists    : $(image_exists && echo yes || echo no)"
    echo "Container exists: $(container_exists && echo yes || echo no)"
    echo "Container run   : $(container_running && echo yes || echo no)"
    echo "KR open         : $(market_open_label KR)"
    echo "US open         : $(market_open_label US)"
    echo "Auto build      : $AUTO_BUILD_IMAGE"
    echo "Auto shutdown   : $AUTO_SHUTDOWN"
    echo "Shutdown cmd    : $SHUTDOWN_COMMAND"
}

print_schedule_summary() {
    cat <<EOF
subscriber Docker 실제 매매 시간 기준 운용 요약

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

[Docker 동작]
- 설치 시 현재 설정으로 컨테이너를 1회 생성합니다.
- 장 시작 시 docker start 로 subscriber 컨테이너를 띄웁니다.
- 장 종료 시 docker stop 으로 중지하고 컨테이너는 유지합니다.
- US는 서머타임(EDT)/표준시(EST) 전환 때문에 시작/중지 후보 시간을 둘 다 등록합니다.
- 실제 시작/중지는 실행 시점에 이미지 안의 trading.market_hours 로 다시 확인합니다.
- .env 변경 사항은 컨테이너 재생성이 필요하므로 스크립트를 다시 설치하면 최신 설정으로 재생성합니다.

[선택 옵션]
- AUTO_BUILD_IMAGE=true 이면 이미지가 없을 때 자동 docker build 합니다.
- AUTO_SHUTDOWN=true 이면 장 종료 후 컨테이너가 중지된 시점에 시스템 종료를 시도합니다.
- 종료 전 KR/US 둘 다 닫혀 있는지 다시 확인합니다.
EOF
}

print_runtime_ready_summary() {
    cat <<EOF
subscriber Docker 런타임 준비가 완료되었습니다.

[현재 상태]
- 프로젝트 경로: $PROJECT_DIR
- Docker 이미지: $IMAGE_NAME
- 컨테이너 이름: $CONTAINER_NAME
- 환경 파일: $ENV_FILE
- KIS 설정 파일: $KIS_CONFIG_HOST_PATH

[수동 실행 명령]
- 시작: $DOCKER_BIN start "$CONTAINER_NAME"
- 중지: $DOCKER_BIN stop "$CONTAINER_NAME"
- 상태: bash "$SCRIPT_PATH" --status

[cron 자동화가 필요할 때]
- 이후에 자동 스케줄을 설치하려면 다음 명령을 실행하세요.
  bash "$SCRIPT_PATH" --install

설명:
- 이 상태에서는 subscriber 컨테이너 정의만 준비되어 있습니다.
- 실제 장 시간 자동 시작/중지는 crontab 설치 후 활성화됩니다.
EOF
}

generate_managed_block() {
    local script_path_q project_dir_q docker_bin_q image_name_q container_name_q
    local env_file_q log_dir_q runtime_dir_q kis_host_q kis_container_q
    local cred_host_q cred_container_q auto_build_q auto_shutdown_q shutdown_cmd_q

    script_path_q="$(shell_quote "$SCRIPT_PATH")"
    project_dir_q="$(shell_quote "$PROJECT_DIR")"
    docker_bin_q="$(shell_quote "$DOCKER_BIN")"
    image_name_q="$(shell_quote "$IMAGE_NAME")"
    container_name_q="$(shell_quote "$CONTAINER_NAME")"
    env_file_q="$(shell_quote "$ENV_FILE")"
    log_dir_q="$(shell_quote "$LOG_DIR")"
    runtime_dir_q="$(shell_quote "$RUNTIME_DIR")"
    kis_host_q="$(shell_quote "$KIS_CONFIG_HOST_PATH")"
    kis_container_q="$(shell_quote "$KIS_CONFIG_CONTAINER_PATH")"
    cred_host_q="$(shell_quote "$CREDENTIALS_HOST_PATH")"
    cred_container_q="$(shell_quote "$CREDENTIALS_CONTAINER_PATH")"
    auto_build_q="$(shell_quote "$AUTO_BUILD_IMAGE")"
    auto_shutdown_q="$(shell_quote "$AUTO_SHUTDOWN")"
    shutdown_cmd_q="$(shell_quote "$SHUTDOWN_COMMAND")"

    cat <<EOF
$BEGIN_MARKER
# Generated by setup_subscriber_docker_crontab.sh on $(date)
# NOTE: This schedule assumes system timezone is Asia/Seoul.
SHELL=/bin/bash
PATH=$(generate_path)

# KR market session
0 9 * * 1-5 cd "$PROJECT_DIR" && PROJECT_DIR=$project_dir_q DOCKER_BIN=$docker_bin_q IMAGE_NAME=$image_name_q CONTAINER_NAME=$container_name_q ENV_FILE=$env_file_q LOG_DIR=$log_dir_q RUNTIME_DIR=$runtime_dir_q KIS_CONFIG_HOST_PATH=$kis_host_q KIS_CONFIG_CONTAINER_PATH=$kis_container_q CREDENTIALS_HOST_PATH=$cred_host_q CREDENTIALS_CONTAINER_PATH=$cred_container_q AUTO_BUILD_IMAGE=$auto_build_q AUTO_SHUTDOWN=$auto_shutdown_q SHUTDOWN_COMMAND=$shutdown_cmd_q bash $script_path_q --cron-start KR
31 15 * * 1-5 cd "$PROJECT_DIR" && PROJECT_DIR=$project_dir_q DOCKER_BIN=$docker_bin_q IMAGE_NAME=$image_name_q CONTAINER_NAME=$container_name_q ENV_FILE=$env_file_q LOG_DIR=$log_dir_q RUNTIME_DIR=$runtime_dir_q KIS_CONFIG_HOST_PATH=$kis_host_q KIS_CONFIG_CONTAINER_PATH=$kis_container_q CREDENTIALS_HOST_PATH=$cred_host_q CREDENTIALS_CONTAINER_PATH=$cred_container_q AUTO_BUILD_IMAGE=$auto_build_q AUTO_SHUTDOWN=$auto_shutdown_q SHUTDOWN_COMMAND=$shutdown_cmd_q bash $script_path_q --cron-stop KR

# US market session
30 22 * * 1-5 cd "$PROJECT_DIR" && PROJECT_DIR=$project_dir_q DOCKER_BIN=$docker_bin_q IMAGE_NAME=$image_name_q CONTAINER_NAME=$container_name_q ENV_FILE=$env_file_q LOG_DIR=$log_dir_q RUNTIME_DIR=$runtime_dir_q KIS_CONFIG_HOST_PATH=$kis_host_q KIS_CONFIG_CONTAINER_PATH=$kis_container_q CREDENTIALS_HOST_PATH=$cred_host_q CREDENTIALS_CONTAINER_PATH=$cred_container_q AUTO_BUILD_IMAGE=$auto_build_q AUTO_SHUTDOWN=$auto_shutdown_q SHUTDOWN_COMMAND=$shutdown_cmd_q bash $script_path_q --cron-start US
30 23 * * 1-5 cd "$PROJECT_DIR" && PROJECT_DIR=$project_dir_q DOCKER_BIN=$docker_bin_q IMAGE_NAME=$image_name_q CONTAINER_NAME=$container_name_q ENV_FILE=$env_file_q LOG_DIR=$log_dir_q RUNTIME_DIR=$runtime_dir_q KIS_CONFIG_HOST_PATH=$kis_host_q KIS_CONFIG_CONTAINER_PATH=$kis_container_q CREDENTIALS_HOST_PATH=$cred_host_q CREDENTIALS_CONTAINER_PATH=$cred_container_q AUTO_BUILD_IMAGE=$auto_build_q AUTO_SHUTDOWN=$auto_shutdown_q SHUTDOWN_COMMAND=$shutdown_cmd_q bash $script_path_q --cron-start US
1 5 * * 2-6 cd "$PROJECT_DIR" && PROJECT_DIR=$project_dir_q DOCKER_BIN=$docker_bin_q IMAGE_NAME=$image_name_q CONTAINER_NAME=$container_name_q ENV_FILE=$env_file_q LOG_DIR=$log_dir_q RUNTIME_DIR=$runtime_dir_q KIS_CONFIG_HOST_PATH=$kis_host_q KIS_CONFIG_CONTAINER_PATH=$kis_container_q CREDENTIALS_HOST_PATH=$cred_host_q CREDENTIALS_CONTAINER_PATH=$cred_container_q AUTO_BUILD_IMAGE=$auto_build_q AUTO_SHUTDOWN=$auto_shutdown_q SHUTDOWN_COMMAND=$shutdown_cmd_q bash $script_path_q --cron-stop US
1 6 * * 2-6 cd "$PROJECT_DIR" && PROJECT_DIR=$project_dir_q DOCKER_BIN=$docker_bin_q IMAGE_NAME=$image_name_q CONTAINER_NAME=$container_name_q ENV_FILE=$env_file_q LOG_DIR=$log_dir_q RUNTIME_DIR=$runtime_dir_q KIS_CONFIG_HOST_PATH=$kis_host_q KIS_CONFIG_CONTAINER_PATH=$kis_container_q CREDENTIALS_HOST_PATH=$cred_host_q CREDENTIALS_CONTAINER_PATH=$cred_container_q AUTO_BUILD_IMAGE=$auto_build_q AUTO_SHUTDOWN=$auto_shutdown_q SHUTDOWN_COMMAND=$shutdown_cmd_q bash $script_path_q --cron-stop US
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
    local backup_file="$PROJECT_DIR/crontab_subscriber_docker_backup_$(date +%Y%m%d_%H%M%S).txt"

    if "$CRONTAB_BIN" -l >/dev/null 2>&1; then
        "$CRONTAB_BIN" -l > "$backup_file"
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

    if "$CRONTAB_BIN" -l >/dev/null 2>&1; then
        "$CRONTAB_BIN" -l > "$temp_current"
    else
        : > "$temp_current"
    fi

    strip_managed_block "$temp_current" "$temp_clean"

    {
        cat "$temp_clean"
        [ -s "$temp_clean" ] && echo
        generate_managed_block
    } > "$temp_current"

    "$CRONTAB_BIN" "$temp_current"
    rm -f "$temp_current" "$temp_clean"
    log_success "subscriber Docker 전용 crontab을 설치했습니다."
}

uninstall_crontab() {
    local temp_current
    local temp_clean
    temp_current="$(mktemp)"
    temp_clean="$(mktemp)"

    if ! "$CRONTAB_BIN" -l >/dev/null 2>&1; then
        log_info "제거할 crontab이 없습니다."
        rm -f "$temp_current" "$temp_clean"
        return 0
    fi

    "$CRONTAB_BIN" -l > "$temp_current"
    strip_managed_block "$temp_current" "$temp_clean"

    if [ -s "$temp_clean" ]; then
        "$CRONTAB_BIN" "$temp_clean"
    else
        "$CRONTAB_BIN" -r
    fi

    rm -f "$temp_current" "$temp_clean"
    log_success "subscriber Docker 전용 crontab을 제거했습니다."
}

show_crontab() {
    if ! "$CRONTAB_BIN" -l >/dev/null 2>&1; then
        log_warn "현재 crontab이 없습니다."
        return 0
    fi

    "$CRONTAB_BIN" -l | awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
        $0 == begin { show = 1 }
        show { print }
        $0 == end { exit }
    '
}

interactive_action_menu() {
    echo -e "${BLUE}================================================${NC}"
    echo -e "${BLUE}   subscriber Docker Crontab 메뉴${NC}"
    echo -e "${BLUE}================================================${NC}"
    echo
    echo "실행할 작업을 선택하세요:"
    echo "  1) 설치"
    echo "  2) 제거"
    echo "  3) 현재 설정 보기"
    echo "  4) 현재 crontab 백업"
    echo "  5) 스케줄 요약 보기"
    echo "  6) Docker subscriber 상태 보기"
    echo

    read -r -p "선택 [1]: " action_choice
    action_choice="$(sanitize_input "${action_choice:-1}")"
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

    read -r -p "Docker 실행 파일 [$DOCKER_BIN]: " input_docker
    DOCKER_BIN="${input_docker:-$DOCKER_BIN}"

    read -r -p "이미지 이름 [$IMAGE_NAME]: " input_image
    IMAGE_NAME="${input_image:-$IMAGE_NAME}"

    read -r -p "컨테이너 이름 [$CONTAINER_NAME]: " input_container
    CONTAINER_NAME="${input_container:-$CONTAINER_NAME}"

    read -r -p ".env 파일 경로 [$ENV_FILE]: " input_env
    ENV_FILE="${input_env:-$ENV_FILE}"

    read -r -p "KIS 설정 파일 경로 [$KIS_CONFIG_HOST_PATH]: " input_kis
    KIS_CONFIG_HOST_PATH="${input_kis:-$KIS_CONFIG_HOST_PATH}"

    read -r -p "KIS 설정 컨테이너 경로 [$KIS_CONFIG_CONTAINER_PATH]: " input_kis_container
    KIS_CONFIG_CONTAINER_PATH="${input_kis_container:-$KIS_CONFIG_CONTAINER_PATH}"

    initialize_credentials_path
    read -r -p "GCP 자격증명 파일 경로 [${CREDENTIALS_HOST_PATH:-없음}]: " input_creds
    if [ -n "${input_creds:-}" ]; then
        CREDENTIALS_HOST_PATH="$input_creds"
    fi

    read -r -p "GCP 자격증명 컨테이너 경로 [$CREDENTIALS_CONTAINER_PATH]: " input_creds_container
    CREDENTIALS_CONTAINER_PATH="${input_creds_container:-$CREDENTIALS_CONTAINER_PATH}"

    read -r -p "로그 디렉토리 [$LOG_DIR]: " input_log
    LOG_DIR="${input_log:-$LOG_DIR}"

    read -r -p "런타임 디렉토리 [$RUNTIME_DIR]: " input_runtime
    RUNTIME_DIR="${input_runtime:-$RUNTIME_DIR}"
}

interactive_install_setup() {
    echo -e "${BLUE}================================================${NC}"
    echo -e "${BLUE}   subscriber Docker Crontab 설정 도구${NC}"
    echo -e "${BLUE}================================================${NC}"
    interactive_common_settings

    read -r -p "이미지가 없으면 자동으로 docker build 할까요? [Y/n]: " input_build
    AUTO_BUILD_IMAGE="$(normalize_bool "${input_build:-true}")"

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
  -h, --help            도움말 표시
  -i, --install         Docker crontab 설치
  -u, --uninstall       Docker crontab 제거
  -s, --show            현재 설치된 관리 블록 표시
  -b, --backup          현재 crontab 백업
  --prepare-runtime     Docker 이미지/컨테이너 정의만 준비하고 cron은 건너뜀
  --install-cron-only   이미 준비된 Docker 런타임에 cron 스케줄만 설치
  --summary             실제 매매 시간 및 Docker cron 스케줄 요약 출력
  --status              현재 Docker subscriber 상태 출력
  --enable-auto-build   이미지가 없으면 자동 docker build
  --disable-auto-build  자동 docker build 비활성화
  --enable-auto-shutdown
                       장 종료 후 조건 충족 시 시스템 종료 활성화
  --disable-auto-shutdown
                       자동 시스템 종료 비활성화
  --shutdown-command CMD
                       자동 종료 시 사용할 명령 지정
  --non-interactive     대화형 입력 건너뛰기

내부용 옵션:
  --cron-start KR|US
  --cron-stop KR|US

환경 변수:
  PROJECT_DIR                프로젝트 경로
  DOCKER_BIN                 Docker 실행 파일
  CRONTAB_BIN                crontab 실행 파일
  TIMEDATECTL_BIN            timedatectl 실행 파일
  SUDO_BIN                   sudo 실행 파일
  IMAGE_NAME                 Docker 이미지 이름
  CONTAINER_NAME             Docker 컨테이너 이름
  ENV_FILE                   .env 파일 경로
  KIS_CONFIG_HOST_PATH       호스트 KIS 설정 파일
  KIS_CONFIG_CONTAINER_PATH  컨테이너 KIS 설정 경로
  CREDENTIALS_HOST_PATH      호스트 GCP 자격증명 파일
  CREDENTIALS_CONTAINER_PATH 컨테이너 GCP 자격증명 경로
  LOG_DIR                    로그 디렉토리
  RUNTIME_DIR                런타임 디렉토리
  AUTO_BUILD_IMAGE           true | false
  AUTO_SHUTDOWN              true | false
  AUTO_CONFIRM_TIMEZONE_CHANGE true | false
  SHUTDOWN_COMMAND           종료 명령

예시:
  $0
  $0 --summary
  $0 --install --non-interactive
  IMAGE_NAME=my-subscriber $0 --status
EOF
}

main() {
    local action="install"
    local interactive=true
    local cron_market=""
    local action_explicit=false

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
            --prepare-runtime)
                action="prepare-runtime"
                action_explicit=true
                ;;
            --install-cron-only)
                action="install-cron-only"
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
            --enable-auto-build)
                AUTO_BUILD_IMAGE="true"
                ;;
            --disable-auto-build)
                AUTO_BUILD_IMAGE="false"
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
                interactive_install_setup
            else
                validate_environment
            fi
            prepare_runtime
            install_cron_schedule
            echo
            print_schedule_summary
            ;;
        prepare-runtime)
            if $interactive; then
                interactive_common_settings
            fi
            prepare_runtime
            echo
            print_runtime_ready_summary
            ;;
        install-cron-only)
            if $interactive; then
                interactive_common_settings
                validate_environment
                read -r -p "subscriber Docker cron 스케줄을 설치할까요? (y/N): " confirm
                if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
                    log_info "취소되었습니다."
                    exit 0
                fi
            else
                validate_environment
            fi
            install_cron_schedule
            echo
            print_schedule_summary
            ;;
        uninstall)
            if $interactive; then
                interactive_common_settings
            fi
            validate_environment
            if $interactive; then
                read -r -p "subscriber Docker crontab을 제거할까요? (y/N): " confirm
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
            start_container "$cron_market"
            ;;
        cron-stop)
            validate_environment
            if [ "$cron_market" != "KR" ] && [ "$cron_market" != "US" ]; then
                log_error "--cron-stop 뒤에는 KR 또는 US 가 필요합니다."
                exit 1
            fi
            stop_container "$cron_market"
            maybe_shutdown_system "$cron_market"
            ;;
    esac
}

main "$@"
