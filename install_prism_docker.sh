#!/usr/bin/env bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

SCRIPT_VERSION="2026-04-11"
DEFAULT_REPO_OWNER="${PRISM_INSTALL_REPO_OWNER:-tkgo11}"
DEFAULT_REPO_NAME="${PRISM_INSTALL_REPO_NAME:-prism-insight-light}"
DEFAULT_ARCHIVE_URL="${PRISM_INSTALL_ARCHIVE_URL:-https://github.com/${DEFAULT_REPO_OWNER}/${DEFAULT_REPO_NAME}/archive/refs/heads/main.tar.gz}"
DEFAULT_INSTALL_DIR="${INSTALL_DIR:-$HOME/prism-insight-light}"
DEFAULT_DOCKER_BIN="${DOCKER_BIN:-docker}"
DEFAULT_IMAGE_NAME="${IMAGE_NAME:-pubsub-trader}"
DEFAULT_CONTAINER_NAME="${CONTAINER_NAME:-prism-insight-subscriber}"
DEFAULT_AUTO_BUILD_IMAGE="${AUTO_BUILD_IMAGE:-true}"
DEFAULT_AUTO_SHUTDOWN="${AUTO_SHUTDOWN:-false}"
DEFAULT_SHUTDOWN_COMMAND="${SHUTDOWN_COMMAND:-sudo shutdown -h now}"
DEFAULT_KIS_SETUP_MODE="${KIS_SETUP_MODE:-guided}"
DEFAULT_KIS_MY_AGENT="${KIS_MY_AGENT:-Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36}"

ARCHIVE_URL="$DEFAULT_ARCHIVE_URL"
INSTALL_DIR="$DEFAULT_INSTALL_DIR"
PROJECT_DIR=""
REPO_DIR=""
DOCKER_BIN="$DEFAULT_DOCKER_BIN"
IMAGE_NAME="$DEFAULT_IMAGE_NAME"
CONTAINER_NAME="$DEFAULT_CONTAINER_NAME"
AUTO_BUILD_IMAGE="$DEFAULT_AUTO_BUILD_IMAGE"
AUTO_SHUTDOWN="$DEFAULT_AUTO_SHUTDOWN"
SHUTDOWN_COMMAND="$DEFAULT_SHUTDOWN_COMMAND"
INSTALL_CRON=""
NON_INTERACTIVE=false
KIS_SETUP_MODE="$DEFAULT_KIS_SETUP_MODE"
WORK_DIR=""
DOWNLOAD_TOOL=""
LOG_DIR=""
RUNTIME_DIR=""
ENV_FILE=""
KIS_CONFIG_PATH=""
KIS_CONFIG_CONTAINER_PATH="/app/trading/config/kis_devlp.yaml"
CREDENTIALS_CONTAINER_PATH="/app/runtime/gcp-credentials.json"
KIS_EDITOR_COMMAND="${KIS_EDITOR:-${VISUAL:-${EDITOR:-}}}"

log_info() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1" >&2
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

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

require_command() {
    if ! command_exists "$1"; then
        log_error "필수 명령을 찾을 수 없습니다: $1"
        exit 1
    fi
}

prompt_with_default() {
    local prompt="$1"
    local default_value="${2:-}"
    local response=""

    if $NON_INTERACTIVE; then
        printf '%s' "$default_value"
        return 0
    fi

    read -r -p "$prompt [$default_value]: " response
    printf '%s' "${response:-$default_value}"
}

prompt_yes_no() {
    local prompt="$1"
    local default_value="${2:-N}"
    local normalized_default
    local response=""

    normalized_default="$(normalize_bool "$default_value")"
    if $NON_INTERACTIVE; then
        printf '%s' "$normalized_default"
        return 0
    fi

    if [ "$normalized_default" = "true" ]; then
        read -r -p "$prompt [Y/n]: " response
        response="${response:-Y}"
    else
        read -r -p "$prompt [y/N]: " response
        response="${response:-N}"
    fi

    printf '%s' "$(normalize_bool "$response")"
}

looks_like_repo() {
    local candidate="$1"
    [ -f "$candidate/Dockerfile" ] && \
        [ -f "$candidate/setup_subscriber_docker_crontab.sh" ] && \
        [ -f "$candidate/.env.example" ] && \
        [ -f "$candidate/trading/config/kis_devlp.yaml.example" ]
}

ensure_supported_host() {
    local detected_host_os
    detected_host_os="${PRISM_INSTALL_HOST_OS:-$(uname -s 2>/dev/null || echo unknown)}"

    case "$detected_host_os" in
        Linux*) ;;
        *)
            log_error "이 설치 스크립트는 Linux 호스트용입니다. 현재 환경에서는 지원하지 않습니다."
            log_error "Linux 또는 Linux 호환 Bash 환경에서 다시 실행해주세요."
            exit 1
            ;;
    esac

    require_command bash
    require_command tar

    if command_exists curl; then
        DOWNLOAD_TOOL="curl"
    elif command_exists wget; then
        DOWNLOAD_TOOL="wget"
    else
        log_error "curl 또는 wget 중 하나가 필요합니다."
        exit 1
    fi
}

cleanup_work_dir() {
    if [ -n "$WORK_DIR" ] && [ -d "$WORK_DIR" ]; then
        rm -rf "$WORK_DIR"
    fi
}

trap cleanup_work_dir EXIT

read_env_value() {
    local file_path="$1"
    local key="$2"

    [ -f "$file_path" ] || return 1

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
    ' "$file_path"
}

upsert_env_value() {
    local file_path="$1"
    local key="$2"
    local value="$3"
    local temp_file
    temp_file="$(mktemp)"

    awk -F= -v key="$key" -v value="$value" '
        BEGIN { updated = 0 }
        $1 == key {
            print key "=" value
            updated = 1
            next
        }
        { print }
        END {
            if (!updated) {
                print key "=" value
            }
        }
    ' "$file_path" > "$temp_file"

    mv "$temp_file" "$file_path"
}

env_value_needs_prompt() {
    local value="${1:-}"
    case "$value" in
        ''|your-project-id|your-subscription-id|/absolute/path/to/service-account.json)
            return 0
            ;;
        *)
            return 1
            ;;
    esac
}

kis_config_has_placeholders() {
    local file_path="$1"
    grep -Eq '입력하세요|12345678|23456789|34567890|실전-메인|실전-서브|모의-메인' "$file_path"
}

ensure_project_checkout() {
    if [ -n "$REPO_DIR" ]; then
        if ! looks_like_repo "$REPO_DIR"; then
            log_error "지정한 repo 경로가 PRISM-INSIGHT Docker 런타임 구조와 일치하지 않습니다: $REPO_DIR"
            exit 1
        fi
        PROJECT_DIR="$REPO_DIR"
        log_info "기존 체크아웃을 재사용합니다: $PROJECT_DIR"
        return 0
    fi

    if [ -d "$INSTALL_DIR" ] && looks_like_repo "$INSTALL_DIR"; then
        if [ "$(prompt_yes_no "기존 설치 디렉토리를 재사용할까요?" Y)" = "true" ]; then
            PROJECT_DIR="$INSTALL_DIR"
            log_info "기존 설치 디렉토리를 재사용합니다: $PROJECT_DIR"
            return 0
        fi
        log_error "기존 설치를 덮어쓰지 않습니다. 다른 --install-dir 경로를 사용하거나 기존 디렉토리를 재사용하세요."
        exit 1
    fi

    if [ -e "$INSTALL_DIR" ] && [ ! -d "$INSTALL_DIR" ]; then
        log_error "설치 경로가 디렉토리가 아닙니다: $INSTALL_DIR"
        exit 1
    fi

    if [ -d "$INSTALL_DIR" ] && [ -n "$(find "$INSTALL_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
        log_error "설치 경로가 비어 있지 않고 기존 PRISM-INSIGHT 체크아웃도 아닙니다: $INSTALL_DIR"
        log_error "데이터 손실 방지를 위해 중단합니다. 비어 있는 새 디렉토리를 지정해주세요."
        exit 1
    fi

    WORK_DIR="$(mktemp -d)"
    local archive_path="$WORK_DIR/prism-insight.tar.gz"
    local extract_dir="$WORK_DIR/extract"
    local extracted_root

    log_info "아카이브 URL: $ARCHIVE_URL"

    if [ "$DOWNLOAD_TOOL" = "curl" ]; then
        curl -fsSL "$ARCHIVE_URL" -o "$archive_path"
    else
        wget -qO "$archive_path" "$ARCHIVE_URL"
    fi

    mkdir -p "$extract_dir"
    tar -xzf "$archive_path" -C "$extract_dir"
    extracted_root="$(find "$extract_dir" -mindepth 1 -maxdepth 1 -type d | head -n 1)"

    if [ -z "$extracted_root" ] || ! looks_like_repo "$extracted_root"; then
        log_error "다운로드한 아카이브에서 예상한 프로젝트 구조를 찾지 못했습니다."
        exit 1
    fi

    mkdir -p "$INSTALL_DIR"
    cp -a "$extracted_root"/. "$INSTALL_DIR"/
    PROJECT_DIR="$INSTALL_DIR"
    log_success "프로젝트를 설치했습니다: $PROJECT_DIR"
}

ensure_repo_paths() {
    ENV_FILE="$PROJECT_DIR/.env"
    KIS_CONFIG_PATH="$PROJECT_DIR/trading/config/kis_devlp.yaml"
    LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
    RUNTIME_DIR="${RUNTIME_DIR:-$PROJECT_DIR/runtime}"
}

configure_env_file() {
    local should_replace="false"
    local project_id subscription_id credentials_path

    if [ ! -f "$ENV_FILE" ]; then
        cp "$PROJECT_DIR/.env.example" "$ENV_FILE"
        log_success ".env 파일을 생성했습니다."
    elif ! env_value_needs_prompt "$(read_env_value "$ENV_FILE" GCP_PROJECT_ID || true)" \
        && ! env_value_needs_prompt "$(read_env_value "$ENV_FILE" GCP_PUBSUB_SUBSCRIPTION_ID || true)" \
        && ! env_value_needs_prompt "$(read_env_value "$ENV_FILE" GCP_CREDENTIALS_PATH || true)"; then
        should_replace="$(prompt_yes_no "기존 .env 파일을 그대로 사용할까요?" Y)"
        if [ "$should_replace" = "true" ]; then
            log_info "기존 .env 파일을 유지합니다."
            return 0
        fi
    fi

    project_id="${GCP_PROJECT_ID:-$(read_env_value "$ENV_FILE" GCP_PROJECT_ID || true)}"
    if env_value_needs_prompt "$project_id"; then
        project_id="$(prompt_with_default "GCP 프로젝트 ID" "")"
    fi

    subscription_id="${GCP_PUBSUB_SUBSCRIPTION_ID:-$(read_env_value "$ENV_FILE" GCP_PUBSUB_SUBSCRIPTION_ID || true)}"
    if env_value_needs_prompt "$subscription_id"; then
        subscription_id="$(prompt_with_default "Pub/Sub subscription ID" "")"
    fi

    credentials_path="${GCP_CREDENTIALS_PATH:-$(read_env_value "$ENV_FILE" GCP_CREDENTIALS_PATH || true)}"
    if env_value_needs_prompt "$credentials_path"; then
        credentials_path="$(prompt_with_default "GCP 서비스 계정 JSON 절대 경로" "")"
    fi

    if [ -z "$project_id" ] || [ -z "$subscription_id" ] || [ -z "$credentials_path" ]; then
        log_error ".env 필수 값이 비어 있습니다."
        exit 1
    fi

    if [ ! -f "$credentials_path" ]; then
        log_error "GCP 자격증명 파일을 찾을 수 없습니다: $credentials_path"
        exit 1
    fi

    upsert_env_value "$ENV_FILE" GCP_PROJECT_ID "$project_id"
    upsert_env_value "$ENV_FILE" GCP_PUBSUB_SUBSCRIPTION_ID "$subscription_id"
    upsert_env_value "$ENV_FILE" GCP_CREDENTIALS_PATH "$credentials_path"
    log_success ".env 필수 값을 업데이트했습니다."
}

write_guided_kis_config() {
    local kis_default_mode kis_my_app kis_my_sec kis_paper_app kis_paper_sec
    local kis_my_htsid kis_account_name kis_account_mode kis_account_market kis_account_number
    local kis_account_product kis_my_agent

    kis_default_mode="${KIS_DEFAULT_MODE:-demo}"
    kis_default_mode="$(prompt_with_default "기본 매매 모드 (demo/real)" "$kis_default_mode")"
    kis_my_app="$(prompt_with_default "실전 App Key" "${KIS_MY_APP:-}")"
    kis_my_sec="$(prompt_with_default "실전 App Secret" "${KIS_MY_SEC:-}")"
    kis_paper_app="$(prompt_with_default "모의투자 App Key" "${KIS_PAPER_APP:-}")"
    kis_paper_sec="$(prompt_with_default "모의투자 App Secret" "${KIS_PAPER_SEC:-}")"
    kis_my_htsid="$(prompt_with_default "HTS 로그인 ID" "${KIS_MY_HTSID:-}")"
    kis_account_name="$(prompt_with_default "기본 계좌 이름" "${KIS_ACCOUNT_NAME:-메인계좌}")"
    kis_account_mode="$(prompt_with_default "기본 계좌 모드 (demo/real)" "${KIS_ACCOUNT_MODE:-$kis_default_mode}")"
    kis_account_market="$(prompt_with_default "기본 계좌 시장 (kr/us/all)" "${KIS_ACCOUNT_MARKET:-all}")"
    kis_account_number="$(prompt_with_default "기본 계좌번호 앞 8자리" "${KIS_ACCOUNT_NUMBER:-}")"
    kis_account_product="$(prompt_with_default "기본 계좌 상품코드" "${KIS_ACCOUNT_PRODUCT:-01}")"
    kis_my_agent="$(prompt_with_default "User-Agent" "${KIS_MY_AGENT:-$DEFAULT_KIS_MY_AGENT}")"

    if [ -z "$kis_my_app" ] || [ -z "$kis_my_sec" ] || [ -z "$kis_paper_app" ] || [ -z "$kis_paper_sec" ] || [ -z "$kis_my_htsid" ] || [ -z "$kis_account_number" ]; then
        log_error "가이드형 KIS 설정에 필요한 값이 비어 있습니다."
        exit 1
    fi

    cat > "$KIS_CONFIG_PATH" <<EOF
# Guided common-case configuration generated by install_prism_docker.sh
default_unit_amount: 10000
default_unit_amount_usd: 100
auto_trading: true
default_mode: "$kis_default_mode"
default_product_code: "$kis_account_product"
my_app: "$kis_my_app"
my_sec: "$kis_my_sec"
paper_app: "$kis_paper_app"
paper_sec: "$kis_paper_sec"
my_htsid: "$kis_my_htsid"
accounts:
  - name: "$kis_account_name"
    mode: "$kis_account_mode"
    market: "$kis_account_market"
    account: "$kis_account_number"
    product: "$kis_account_product"
prod: "https://openapi.koreainvestment.com:9443"
ops: "ws://ops.koreainvestment.com:21000"
vps: "https://openapivts.koreainvestment.com:29443"
vops: "ws://ops.koreainvestment.com:31000"
my_token: ""
my_agent: "$kis_my_agent"
EOF

    log_success "가이드형 KIS 설정 파일을 작성했습니다."
}

launch_kis_editor() {
    if [ -n "$KIS_EDITOR_COMMAND" ]; then
        if command_exists "$KIS_EDITOR_COMMAND"; then
            "$KIS_EDITOR_COMMAND" "$KIS_CONFIG_PATH"
            return 0
        elif [ -x "$KIS_EDITOR_COMMAND" ]; then
            "$KIS_EDITOR_COMMAND" "$KIS_CONFIG_PATH"
            return 0
        fi
    fi
    return 1
}

configure_manual_kis_config() {
    if ! $NON_INTERACTIVE; then
        echo
        echo "고급 KIS 설정은 예제 YAML을 직접 수정해야 합니다."
        echo "- 다중 계좌, 계좌별 app_key/app_secret 조합이 필요하면 이 경로를 사용하세요."
        echo "- 수정이 끝난 뒤 예제 placeholder가 남아 있으면 계속 진행할 수 없습니다."
    fi

    if launch_kis_editor; then
        log_info "편집기를 실행했습니다: $KIS_EDITOR_COMMAND"
    elif $NON_INTERACTIVE; then
        log_error "비대화형 manual KIS 모드에서는 KIS_EDITOR/EDITOR/VISUAL 이 필요합니다."
        exit 1
    else
        read -r -p "'$KIS_CONFIG_PATH' 파일을 수정한 뒤 Enter 키를 누르세요." _unused
    fi

    if kis_config_has_placeholders "$KIS_CONFIG_PATH"; then
        log_error "KIS 설정 파일에 예제 placeholder가 아직 남아 있습니다."
        log_error "'입력하세요' 또는 예제 계좌 번호를 실제 값으로 바꾼 뒤 다시 실행해주세요."
        exit 1
    fi

    log_success "수동 KIS 설정 검증을 통과했습니다."
}

configure_kis_config() {
    local should_replace="false"

    mkdir -p "$(dirname "$KIS_CONFIG_PATH")"

    if [ ! -f "$KIS_CONFIG_PATH" ]; then
        cp "$PROJECT_DIR/trading/config/kis_devlp.yaml.example" "$KIS_CONFIG_PATH"
        log_success "KIS 설정 파일을 예제에서 생성했습니다."
    elif ! kis_config_has_placeholders "$KIS_CONFIG_PATH"; then
        should_replace="$(prompt_yes_no "기존 KIS 설정 파일을 그대로 사용할까요?" Y)"
        if [ "$should_replace" = "true" ]; then
            log_info "기존 KIS 설정 파일을 유지합니다."
            return 0
        fi
    fi

    if $NON_INTERACTIVE; then
        KIS_SETUP_MODE="${KIS_SETUP_MODE:-guided}"
    else
        echo
        echo "KIS 설정 방식을 선택하세요:"
        echo "  1) guided - 기본 단일 계좌/공통값 프롬프트"
        echo "  2) manual  - 예제 YAML 직접 수정 (다중 계좌 권장)"
        read -r -p "선택 [${KIS_SETUP_MODE}]: " kis_mode_choice
        case "${kis_mode_choice:-$KIS_SETUP_MODE}" in
            1|guided|GUIDED)
                KIS_SETUP_MODE="guided"
                ;;
            2|manual|MANUAL)
                KIS_SETUP_MODE="manual"
                ;;
            *)
                log_warn "알 수 없는 선택이라 guided 모드로 진행합니다."
                KIS_SETUP_MODE="guided"
                ;;
        esac
    fi

    case "$KIS_SETUP_MODE" in
        guided)
            write_guided_kis_config
            ;;
        manual)
            configure_manual_kis_config
            ;;
        *)
            log_error "지원하지 않는 KIS 설정 모드입니다: $KIS_SETUP_MODE"
            exit 1
            ;;
    esac
}

configure_runtime_settings() {
    local input

    input="$(prompt_with_default "Docker 실행 파일" "$DOCKER_BIN")"
    DOCKER_BIN="$input"
    input="$(prompt_with_default "Docker 이미지 이름" "$IMAGE_NAME")"
    IMAGE_NAME="$input"
    input="$(prompt_with_default "Docker 컨테이너 이름" "$CONTAINER_NAME")"
    CONTAINER_NAME="$input"
    input="$(prompt_with_default "로그 디렉토리" "$LOG_DIR")"
    LOG_DIR="$input"
    input="$(prompt_with_default "런타임 디렉토리" "$RUNTIME_DIR")"
    RUNTIME_DIR="$input"

    AUTO_BUILD_IMAGE="$(prompt_yes_no "이미지가 없으면 자동으로 docker build 할까요?" "$AUTO_BUILD_IMAGE")"
    AUTO_SHUTDOWN="$(prompt_yes_no "장 종료 후 시스템 종료를 사용할까요?" "$AUTO_SHUTDOWN")"
    if [ "$AUTO_SHUTDOWN" = "true" ]; then
        SHUTDOWN_COMMAND="$(prompt_with_default "종료 명령" "$SHUTDOWN_COMMAND")"
    fi

    INSTALL_CRON="${INSTALL_CRON:-$(prompt_yes_no "시장 시간 자동화를 위해 crontab을 설치할까요?" N)}"
}

run_repo_setup() {
    local action="$1"
    local setup_script="$PROJECT_DIR/setup_subscriber_docker_crontab.sh"
    local -a cmd=(bash "$setup_script" --non-interactive)

    case "$action" in
        prepare-runtime)
            cmd+=(--prepare-runtime)
            ;;
        install-cron-only)
            cmd+=(--install-cron-only)
            ;;
        *)
            log_error "지원하지 않는 repo setup action 입니다: $action"
            exit 1
            ;;
    esac

    env \
        PROJECT_DIR="$PROJECT_DIR" \
        DOCKER_BIN="$DOCKER_BIN" \
        IMAGE_NAME="$IMAGE_NAME" \
        CONTAINER_NAME="$CONTAINER_NAME" \
        ENV_FILE="$ENV_FILE" \
        LOG_DIR="$LOG_DIR" \
        RUNTIME_DIR="$RUNTIME_DIR" \
        KIS_CONFIG_HOST_PATH="$KIS_CONFIG_PATH" \
        KIS_CONFIG_CONTAINER_PATH="$KIS_CONFIG_CONTAINER_PATH" \
        CREDENTIALS_HOST_PATH="$(read_env_value "$ENV_FILE" GCP_CREDENTIALS_PATH || true)" \
        CREDENTIALS_CONTAINER_PATH="$CREDENTIALS_CONTAINER_PATH" \
        AUTO_BUILD_IMAGE="$AUTO_BUILD_IMAGE" \
        AUTO_SHUTDOWN="$AUTO_SHUTDOWN" \
        SHUTDOWN_COMMAND="$SHUTDOWN_COMMAND" \
        "${cmd[@]}"
}

print_final_summary() {
    cat <<EOF

설치가 완료되었습니다.

[설치 정보]
- 설치 스크립트 버전: $SCRIPT_VERSION
- 프로젝트 경로: $PROJECT_DIR
- Docker 이미지: $IMAGE_NAME
- Docker 컨테이너: $CONTAINER_NAME
- .env 경로: $ENV_FILE
- KIS 설정 경로: $KIS_CONFIG_PATH
- cron 설치 여부: $INSTALL_CRON

[다음 단계]
- 컨테이너 시작: $DOCKER_BIN start "$CONTAINER_NAME"
- 컨테이너 중지: $DOCKER_BIN stop "$CONTAINER_NAME"
- 상태 확인: bash "$PROJECT_DIR/setup_subscriber_docker_crontab.sh" --status
EOF

    if [ "$INSTALL_CRON" = "true" ]; then
        cat <<EOF
- 현재 설치는 cron 자동화까지 완료되었습니다.
EOF
    else
        cat <<EOF
- cron 자동화는 설치하지 않았습니다.
- 이후 자동 스케줄이 필요하면:
  bash "$PROJECT_DIR/setup_subscriber_docker_crontab.sh" --install
EOF
    fi

    cat <<EOF

[주의]
- .env 또는 KIS 설정을 바꾼 뒤에는 이 스크립트를 다시 실행하면 컨테이너 정의를 갱신할 수 있습니다.
- Linux 호스트 기준으로 검증된 설치 흐름입니다.
EOF
}

show_help() {
    cat <<EOF
사용법: $0 [옵션]

옵션:
  --install-dir PATH     다운로드/설치 디렉토리 (기본값: $DEFAULT_INSTALL_DIR)
  --repo-dir PATH        기존 체크아웃 재사용 (다운로드 건너뜀)
  --archive-url URL      다운로드할 tar.gz URL 오버라이드
  --with-cron            cron 설치를 강제로 활성화
  --without-cron         cron 설치를 건너뜀
  --guided-kis           guided KIS 설정 사용
  --manual-kis           manual KIS 설정 사용
  --non-interactive      환경 변수/기본값만 사용하여 비대화형 실행
  -h, --help             도움말 표시

환경 변수:
  PRISM_INSTALL_ARCHIVE_URL / INSTALL_DIR
  GCP_PROJECT_ID / GCP_PUBSUB_SUBSCRIPTION_ID / GCP_CREDENTIALS_PATH
  DOCKER_BIN / IMAGE_NAME / CONTAINER_NAME / LOG_DIR / RUNTIME_DIR
  AUTO_BUILD_IMAGE / AUTO_SHUTDOWN / SHUTDOWN_COMMAND
  KIS_SETUP_MODE=guided|manual
  KIS_EDITOR=<manual 모드 편집기>
  KIS_DEFAULT_MODE / KIS_MY_APP / KIS_MY_SEC / KIS_PAPER_APP / KIS_PAPER_SEC
  KIS_MY_HTSID / KIS_ACCOUNT_NAME / KIS_ACCOUNT_MODE / KIS_ACCOUNT_MARKET
  KIS_ACCOUNT_NUMBER / KIS_ACCOUNT_PRODUCT / KIS_MY_AGENT

예시:
  curl -fsSL https://raw.githubusercontent.com/$DEFAULT_REPO_OWNER/$DEFAULT_REPO_NAME/main/install_prism_docker.sh | bash
  bash $0 --install-dir /opt/prism-insight-light
  KIS_SETUP_MODE=manual KIS_EDITOR=vim bash $0 --with-cron
EOF
}

main() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --install-dir)
                shift
                INSTALL_DIR="${1:-}"
                ;;
            --repo-dir)
                shift
                REPO_DIR="${1:-}"
                ;;
            --archive-url)
                shift
                ARCHIVE_URL="${1:-}"
                ;;
            --with-cron)
                INSTALL_CRON="true"
                ;;
            --without-cron)
                INSTALL_CRON="false"
                ;;
            --guided-kis)
                KIS_SETUP_MODE="guided"
                ;;
            --manual-kis)
                KIS_SETUP_MODE="manual"
                ;;
            --non-interactive)
                NON_INTERACTIVE=true
                ;;
            -h|--help)
                show_help
                exit 0
                ;;
            *)
                log_error "알 수 없는 옵션입니다: $1"
                show_help
                exit 1
                ;;
        esac
        shift
    done

    echo -e "${BLUE}================================================${NC}"
    echo -e "${BLUE}   PRISM-INSIGHT Docker One-Click Installer${NC}"
    echo -e "${BLUE}================================================${NC}"

    ensure_supported_host

    if [ -n "$REPO_DIR" ]; then
        INSTALL_DIR="$REPO_DIR"
    elif ! $NON_INTERACTIVE; then
        INSTALL_DIR="$(prompt_with_default "설치 디렉토리" "$INSTALL_DIR")"
    fi

    ensure_project_checkout
    ensure_repo_paths
    configure_env_file
    configure_kis_config
    configure_runtime_settings

    if ! command_exists "$DOCKER_BIN"; then
        log_error "Docker 실행 파일을 찾을 수 없습니다: $DOCKER_BIN"
        exit 1
    fi

    run_repo_setup prepare-runtime

    if [ "$INSTALL_CRON" = "true" ]; then
        run_repo_setup install-cron-only
    fi

    print_final_summary
}

main "$@"
