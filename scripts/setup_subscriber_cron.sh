#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"

MARKER_BEGIN="# >>> PRISM-INSIGHT subscriber cron >>>"
MARKER_END="# <<< PRISM-INSIGHT subscriber cron <<<"

KR_MORNING_START_CRON="${KR_MORNING_START_CRON:-30 9 * * 1-5}"
KR_MORNING_STOP_CRON="${KR_MORNING_STOP_CRON:-0 10 * * 1-5}"
KR_AFTERNOON_START_CRON="${KR_AFTERNOON_START_CRON:-40 15 * * 1-5}"
KR_AFTERNOON_STOP_CRON="${KR_AFTERNOON_STOP_CRON:-10 16 * * 1-5}"
US_EARLY_START_CRON="${US_EARLY_START_CRON:-30 2 * * 2-6}"
US_EARLY_STOP_CRON="${US_EARLY_STOP_CRON:-50 2 * * 2-6}"
US_LATE_START_CRON="${US_LATE_START_CRON:-30 6 * * 2-6}"
US_LATE_STOP_CRON="${US_LATE_STOP_CRON:-50 6 * * 2-6}"
EXECUTION_MODE="${EXECUTION_MODE:-python}"
AUTO_SHUTDOWN="${AUTO_SHUTDOWN:-true}"
PROMPT_MODE="${PROMPT_MODE:-auto}"

LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
RUN_DIR="${RUN_DIR:-$PROJECT_DIR/run}"
PID_FILE="${PID_FILE:-$RUN_DIR/subscriber.pid}"
SUBSCRIBER_SCRIPT="${SUBSCRIBER_SCRIPT:-$PROJECT_DIR/subscriber.py}"
SUBSCRIBER_ARGS="${SUBSCRIBER_ARGS:-}"
PYTHON_PATH="${PYTHON_PATH:-}"
DOCKER_SERVICE="${DOCKER_SERVICE:-subscriber}"
DOCKER_CONTAINER_NAME="${DOCKER_CONTAINER_NAME:-prism_subscriber}"
DOCKER_COMPOSE_FILE="${DOCKER_COMPOSE_FILE:-$PROJECT_DIR/docker-compose.yml}"
DOCKER_BIN="${DOCKER_BIN:-}"
DOCKER_COMPOSE_BIN="${DOCKER_COMPOSE_BIN:-}"

detect_python_path() {
    if [ -n "$PYTHON_PATH" ]; then
        printf '%s\n' "$PYTHON_PATH"
        return
    fi

    if [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
        printf '%s\n' "$PROJECT_DIR/.venv/bin/python"
        return
    fi

    if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
        printf '%s\n' "$PROJECT_DIR/venv/bin/python"
        return
    fi

    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return
    fi

    if command -v python >/dev/null 2>&1; then
        command -v python
        return
    fi

    return 1
}

detect_docker_path() {
    if [ -n "$DOCKER_BIN" ]; then
        printf '%s\n' "$DOCKER_BIN"
        return
    fi

    if command -v docker >/dev/null 2>&1; then
        command -v docker
        return
    fi

    return 1
}

detect_docker_compose_cmd() {
    if [ -n "$DOCKER_COMPOSE_BIN" ]; then
        printf '%s\n' "$DOCKER_COMPOSE_BIN"
        return
    fi

    if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
        printf 'docker compose\n'
        return
    fi

    if command -v docker-compose >/dev/null 2>&1; then
        command -v docker-compose
        return
    fi

    return 1
}

log_info() {
    printf '[INFO] %s\n' "$1"
}

log_warn() {
    printf '[WARN] %s\n' "$1"
}

log_error() {
    printf '[ERROR] %s\n' "$1" >&2
}

log_success() {
    printf '[OK] %s\n' "$1"
}

normalize_boolean() {
    local value="${1:-}"
    value="$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')"

    case "$value" in
        1|true|yes|y|on)
            printf 'true\n'
            ;;
        0|false|no|n|off)
            printf 'false\n'
            ;;
        *)
            return 1
            ;;
    esac
}

AUTO_SHUTDOWN="$(normalize_boolean "$AUTO_SHUTDOWN" 2>/dev/null || printf 'true\n')"

usage() {
    cat <<EOF
Usage: $(basename "$0") [command]

Commands:
  --install     Install managed cron entries for subscriber.py
  --uninstall   Remove managed cron entries
  --show        Show the managed cron block
  --start       Start subscriber.py in the background
  --stop        Stop the managed subscriber.py process
  --status      Show whether subscriber.py is running
  --interactive Force the install wizard
  --non-interactive  Skip prompts and use env/default values
  --help        Show this help text

Defaults:
  KR 09:30-10:00 Monday-Friday
  KR 15:40-16:10 Monday-Friday
  US 02:30-02:50 Tuesday-Saturday
  US 06:30-06:50 Tuesday-Saturday
  EXECUTION_MODE="$EXECUTION_MODE"  # python | docker-compose | docker
  AUTO_SHUTDOWN="$AUTO_SHUTDOWN"    # true | false

Overrides:
  PROJECT_DIR, PYTHON_PATH, LOG_DIR, RUN_DIR, SUBSCRIBER_ARGS,
  EXECUTION_MODE, DOCKER_SERVICE, DOCKER_CONTAINER_NAME,
  DOCKER_COMPOSE_FILE, KR_MORNING_START_CRON, KR_MORNING_STOP_CRON,
  KR_AFTERNOON_START_CRON, KR_AFTERNOON_STOP_CRON,
  US_EARLY_START_CRON, US_EARLY_STOP_CRON, US_LATE_START_CRON,
  US_LATE_STOP_CRON, AUTO_SHUTDOWN

Docker modes:
  EXECUTION_MODE=docker-compose  Uses docker compose up/stop for service '$DOCKER_SERVICE'
  EXECUTION_MODE=docker          Uses docker start/stop for container '$DOCKER_CONTAINER_NAME'
EOF
}

quote_for_crontab() {
    printf "'%s'" "$(printf '%s' "$1" | sed "s/'/'\\\\''/g")"
}

validate_environment() {
    if [ ! -d "$PROJECT_DIR" ]; then
        log_error "Project directory not found: $PROJECT_DIR"
        exit 1
    fi

    mkdir -p "$LOG_DIR" "$RUN_DIR"

    case "$EXECUTION_MODE" in
        python)
            PYTHON_PATH="$(detect_python_path)"
            if [ ! -f "$SUBSCRIBER_SCRIPT" ]; then
                log_error "subscriber.py not found: $SUBSCRIBER_SCRIPT"
                exit 1
            fi

            if [ ! -x "$PYTHON_PATH" ]; then
                log_error "Python executable not found or not executable: $PYTHON_PATH"
                exit 1
            fi
            ;;
        docker-compose)
            if [ ! -f "$DOCKER_COMPOSE_FILE" ]; then
                log_error "Docker Compose file not found: $DOCKER_COMPOSE_FILE"
                exit 1
            fi

            DOCKER_BIN="$(detect_docker_path)"
            DOCKER_COMPOSE_BIN="$(detect_docker_compose_cmd)"
            ;;
        docker)
            DOCKER_BIN="$(detect_docker_path)"
            ;;
        *)
            log_error "Unsupported EXECUTION_MODE: $EXECUTION_MODE"
            exit 1
            ;;
    esac

    AUTO_SHUTDOWN="$(normalize_boolean "$AUTO_SHUTDOWN" 2>/dev/null || {
        log_error "AUTO_SHUTDOWN must be true or false."
        exit 1
    })"
}

require_crontab_command() {
    if ! command -v crontab >/dev/null 2>&1; then
        log_error "crontab command not found. Install cron/cronie on the target Linux host first."
        exit 1
    fi
}

is_running() {
    if [ ! -f "$PID_FILE" ]; then
        return 1
    fi

    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"

    if [ -z "$pid" ]; then
        rm -f "$PID_FILE"
        return 1
    fi

    if kill -0 "$pid" >/dev/null 2>&1; then
        return 0
    fi

    rm -f "$PID_FILE"
    return 1
}

start_subscriber() {
    validate_environment

    case "$EXECUTION_MODE" in
        python)
            if is_running; then
                log_info "subscriber.py is already running with PID $(cat "$PID_FILE")."
                return 0
            fi

            local log_file
            log_file="$LOG_DIR/subscriber_$(date +%Y%m%d).log"
            local args=()

            if [ -n "${SUBSCRIBER_ARGS// }" ]; then
                # Split simple flag strings like "--dry-run".
                read -r -a args <<<"$SUBSCRIBER_ARGS"
            fi

            (
                cd "$PROJECT_DIR"
                nohup "$PYTHON_PATH" "$SUBSCRIBER_SCRIPT" "${args[@]}" >>"$log_file" 2>&1 &
                echo $! >"$PID_FILE"
            )

            sleep 1

            if is_running; then
                log_success "Started subscriber.py with PID $(cat "$PID_FILE")."
                log_info "Logging to $log_file"
                return 0
            fi

            log_error "subscriber.py did not stay running. Check $log_file"
            exit 1
            ;;
        docker-compose)
            local compose_cmd
            compose_cmd="$(detect_docker_compose_cmd)"
            (
                cd "$PROJECT_DIR"
                if [ "$compose_cmd" = "docker compose" ]; then
                    docker compose -f "$DOCKER_COMPOSE_FILE" up -d "$DOCKER_SERVICE"
                else
                    "$compose_cmd" -f "$DOCKER_COMPOSE_FILE" up -d "$DOCKER_SERVICE"
                fi
            )
            log_success "Started Docker Compose service '$DOCKER_SERVICE'."
            ;;
        docker)
            if "$DOCKER_BIN" inspect -f '{{.State.Running}}' "$DOCKER_CONTAINER_NAME" 2>/dev/null | grep -q '^true$'; then
                log_info "Docker container '$DOCKER_CONTAINER_NAME' is already running."
                return 0
            fi
            "$DOCKER_BIN" start "$DOCKER_CONTAINER_NAME" >/dev/null
            log_success "Started Docker container '$DOCKER_CONTAINER_NAME'."
            ;;
    esac
}

stop_subscriber() {
    validate_environment

    case "$EXECUTION_MODE" in
        python)
            if ! is_running; then
                log_info "subscriber.py is not running."
                return 0
            fi

            local pid
            pid="$(cat "$PID_FILE")"

            kill "$pid" >/dev/null 2>&1 || true

            for _ in $(seq 1 10); do
                if ! kill -0 "$pid" >/dev/null 2>&1; then
                    rm -f "$PID_FILE"
                    log_success "Stopped subscriber.py."
                    return 0
                fi
                sleep 1
            done

            log_warn "subscriber.py did not stop after SIGTERM; sending SIGKILL."
            kill -9 "$pid" >/dev/null 2>&1 || true
            rm -f "$PID_FILE"
            log_success "Stopped subscriber.py."
            ;;
        docker-compose)
            local compose_cmd
            compose_cmd="$(detect_docker_compose_cmd)"
            (
                cd "$PROJECT_DIR"
                if [ "$compose_cmd" = "docker compose" ]; then
                    docker compose -f "$DOCKER_COMPOSE_FILE" stop "$DOCKER_SERVICE"
                else
                    "$compose_cmd" -f "$DOCKER_COMPOSE_FILE" stop "$DOCKER_SERVICE"
                fi
            )
            log_success "Stopped Docker Compose service '$DOCKER_SERVICE'."
            ;;
        docker)
            if ! "$DOCKER_BIN" inspect -f '{{.State.Running}}' "$DOCKER_CONTAINER_NAME" 2>/dev/null | grep -q '^true$'; then
                log_info "Docker container '$DOCKER_CONTAINER_NAME' is not running."
                return 0
            fi
            "$DOCKER_BIN" stop "$DOCKER_CONTAINER_NAME" >/dev/null
            log_success "Stopped Docker container '$DOCKER_CONTAINER_NAME'."
            ;;
    esac
}

show_status() {
    validate_environment

    case "$EXECUTION_MODE" in
        python)
            if is_running; then
                printf 'subscriber.py is running (PID %s)\n' "$(cat "$PID_FILE")"
            else
                printf 'subscriber.py is not running\n'
            fi
            ;;
        docker-compose)
            local compose_cmd
            compose_cmd="$(detect_docker_compose_cmd)"
            if [ "$compose_cmd" = "docker compose" ]; then
                docker compose -f "$DOCKER_COMPOSE_FILE" ps "$DOCKER_SERVICE"
            else
                "$compose_cmd" -f "$DOCKER_COMPOSE_FILE" ps "$DOCKER_SERVICE"
            fi
            ;;
        docker)
            "$DOCKER_BIN" ps --filter "name=^/${DOCKER_CONTAINER_NAME}$" --format 'table {{.Names}}\t{{.Status}}\t{{.Image}}'
            ;;
    esac
}

build_path_for_cron() {
    printf '%s\n' "$PATH:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
}

should_prompt_install() {
    case "$PROMPT_MODE" in
        always)
            return 0
            ;;
        never)
            return 1
            ;;
        auto)
            [ -t 0 ] && [ -t 1 ]
            ;;
        *)
            return 1
            ;;
    esac
}

prompt_with_default() {
    local prompt="$1"
    local default_value="$2"
    local response

    read -r -p "$prompt [$default_value]: " response
    if [ -z "$response" ]; then
        printf '%s\n' "$default_value"
    else
        printf '%s\n' "$response"
    fi
}

prompt_yes_no() {
    local prompt="$1"
    local default_value
    default_value="$(normalize_boolean "$2" 2>/dev/null || printf 'true\n')"
    local hint="Y/n"

    if [ "$default_value" = "false" ]; then
        hint="y/N"
    fi

    while true; do
        local response
        read -r -p "$prompt [$hint]: " response

        if [ -z "$response" ]; then
            printf '%s\n' "$default_value"
            return 0
        fi

        if normalized="$(normalize_boolean "$response" 2>/dev/null)"; then
            printf '%s\n' "$normalized"
            return 0
        fi

        log_warn "Please answer yes or no."
    done
}

prompt_execution_mode() {
    local default_mode="$EXECUTION_MODE"

    while true; do
        printf '\nSelect execution mode:\n'
        printf '  1. python\n'
        printf '  2. docker-compose\n'
        printf '  3. docker\n'
        local response
        read -r -p "Choice [$default_mode]: " response

        if [ -z "$response" ]; then
            printf '%s\n' "$default_mode"
            return 0
        fi

        case "$response" in
            1|python)
                printf 'python\n'
                return 0
                ;;
            2|docker-compose|docker_compose|compose)
                printf 'docker-compose\n'
                return 0
                ;;
            3|docker)
                printf 'docker\n'
                return 0
                ;;
        esac

        log_warn "Choose 1, 2, 3, python, docker-compose, or docker."
    done
}

print_schedule_summary() {
    printf '\nCurrent schedule windows (KST):\n'
    printf '  KR morning:   %s -> %s\n' "$KR_MORNING_START_CRON" "$KR_MORNING_STOP_CRON"
    printf '  KR afternoon: %s -> %s\n' "$KR_AFTERNOON_START_CRON" "$KR_AFTERNOON_STOP_CRON"
    printf '  US early:     %s -> %s\n' "$US_EARLY_START_CRON" "$US_EARLY_STOP_CRON"
    printf '  US late:      %s -> %s\n' "$US_LATE_START_CRON" "$US_LATE_STOP_CRON"
    printf '  Auto shutdown: %s\n' "$AUTO_SHUTDOWN"
}

configure_interactively() {
    printf '\nPRISM-INSIGHT subscriber cron setup\n'
    printf 'Press Enter to keep the value shown in brackets.\n'

    EXECUTION_MODE="$(prompt_execution_mode)"

    case "$EXECUTION_MODE" in
        python)
            PYTHON_PATH="$(prompt_with_default "Python path" "$(detect_python_path)")"
            ;;
        docker-compose)
            DOCKER_COMPOSE_FILE="$(prompt_with_default "Docker Compose file" "$DOCKER_COMPOSE_FILE")"
            DOCKER_SERVICE="$(prompt_with_default "Docker Compose service" "$DOCKER_SERVICE")"
            ;;
        docker)
            DOCKER_CONTAINER_NAME="$(prompt_with_default "Docker container name" "$DOCKER_CONTAINER_NAME")"
            ;;
    esac

    SUBSCRIBER_ARGS="$(prompt_with_default "Subscriber arguments (blank for none)" "$SUBSCRIBER_ARGS")"
    LOG_DIR="$(prompt_with_default "Log directory" "$LOG_DIR")"
    RUN_DIR="$(prompt_with_default "Run directory" "$RUN_DIR")"
    AUTO_SHUTDOWN="$(prompt_yes_no "Automatically stop subscriber at the end of each window?" "$AUTO_SHUTDOWN")"

    print_schedule_summary

    if [ "$(prompt_yes_no "Customize schedule windows?" false)" = "true" ]; then
        KR_MORNING_START_CRON="$(prompt_with_default "KR morning start cron" "$KR_MORNING_START_CRON")"
        KR_MORNING_STOP_CRON="$(prompt_with_default "KR morning stop cron" "$KR_MORNING_STOP_CRON")"
        KR_AFTERNOON_START_CRON="$(prompt_with_default "KR afternoon start cron" "$KR_AFTERNOON_START_CRON")"
        KR_AFTERNOON_STOP_CRON="$(prompt_with_default "KR afternoon stop cron" "$KR_AFTERNOON_STOP_CRON")"
        US_EARLY_START_CRON="$(prompt_with_default "US early start cron" "$US_EARLY_START_CRON")"
        US_EARLY_STOP_CRON="$(prompt_with_default "US early stop cron" "$US_EARLY_STOP_CRON")"
        US_LATE_START_CRON="$(prompt_with_default "US late start cron" "$US_LATE_START_CRON")"
        US_LATE_STOP_CRON="$(prompt_with_default "US late stop cron" "$US_LATE_STOP_CRON")"
    fi

    print_schedule_summary

    if [ "$(prompt_yes_no "Install this cron configuration now?" true)" != "true" ]; then
        log_info "Installation canceled."
        exit 0
    fi
}

generate_crontab_entries() {
    cat <<EOF
$MARKER_BEGIN
# Managed by scripts/setup_subscriber_cron.sh
# KST defaults:
# - KR window 1: 09:30-10:00 Monday-Friday
# - KR window 2: 15:40-16:10 Monday-Friday
# - US window 1: 02:30-02:50 Tuesday-Saturday
# - US window 2: 06:30-06:50 Tuesday-Saturday
SHELL=/bin/bash
PATH=$(build_path_for_cron)
PROJECT_DIR=$(quote_for_crontab "$PROJECT_DIR")
PYTHON_PATH=$(quote_for_crontab "$PYTHON_PATH")
LOG_DIR=$(quote_for_crontab "$LOG_DIR")
RUN_DIR=$(quote_for_crontab "$RUN_DIR")
SETUP_SCRIPT=$(quote_for_crontab "$SCRIPT_DIR/setup_subscriber_cron.sh")
SUBSCRIBER_ARGS=$(quote_for_crontab "$SUBSCRIBER_ARGS")
EXECUTION_MODE=$(quote_for_crontab "$EXECUTION_MODE")
AUTO_SHUTDOWN=$(quote_for_crontab "$AUTO_SHUTDOWN")
DOCKER_SERVICE=$(quote_for_crontab "$DOCKER_SERVICE")
DOCKER_CONTAINER_NAME=$(quote_for_crontab "$DOCKER_CONTAINER_NAME")
DOCKER_COMPOSE_FILE=$(quote_for_crontab "$DOCKER_COMPOSE_FILE")

# KR session windows
$KR_MORNING_START_CRON cd "$PROJECT_DIR" && "$SETUP_SCRIPT" --start >> "$LOG_DIR/subscriber_cron.log" 2>&1
$KR_AFTERNOON_START_CRON cd "$PROJECT_DIR" && "$SETUP_SCRIPT" --start >> "$LOG_DIR/subscriber_cron.log" 2>&1

# US session windows
$US_EARLY_START_CRON cd "$PROJECT_DIR" && "$SETUP_SCRIPT" --start >> "$LOG_DIR/subscriber_cron.log" 2>&1
$US_LATE_START_CRON cd "$PROJECT_DIR" && "$SETUP_SCRIPT" --start >> "$LOG_DIR/subscriber_cron.log" 2>&1
EOF

    if [ "$AUTO_SHUTDOWN" = "true" ]; then
        cat <<EOF
# Automatic shutdown windows
$KR_MORNING_STOP_CRON cd "$PROJECT_DIR" && "$SETUP_SCRIPT" --stop >> "$LOG_DIR/subscriber_cron.log" 2>&1
$KR_AFTERNOON_STOP_CRON cd "$PROJECT_DIR" && "$SETUP_SCRIPT" --stop >> "$LOG_DIR/subscriber_cron.log" 2>&1
$US_EARLY_STOP_CRON cd "$PROJECT_DIR" && "$SETUP_SCRIPT" --stop >> "$LOG_DIR/subscriber_cron.log" 2>&1
$US_LATE_STOP_CRON cd "$PROJECT_DIR" && "$SETUP_SCRIPT" --stop >> "$LOG_DIR/subscriber_cron.log" 2>&1
EOF
    else
        cat <<EOF
# Automatic shutdown disabled. Start entries only.
EOF
    fi

    cat <<EOF
$MARKER_END
EOF
}

strip_managed_block() {
    awk -v start="$MARKER_BEGIN" -v end="$MARKER_END" '
        $0 == start { skip = 1; next }
        $0 == end { skip = 0; next }
        !skip { print }
    '
}

install_crontab() {
    validate_environment
    require_crontab_command

    local temp_cron
    temp_cron="$(mktemp)"

    {
        (crontab -l 2>/dev/null || true) | strip_managed_block
        printf '\n'
        generate_crontab_entries
        printf '\n'
    } >"$temp_cron"

    crontab "$temp_cron"
    rm -f "$temp_cron"

    log_success "Installed managed subscriber cron entries."
    log_info "KR 09:30-10:00  -> $KR_MORNING_START_CRON / $KR_MORNING_STOP_CRON"
    log_info "KR 15:40-16:10  -> $KR_AFTERNOON_START_CRON / $KR_AFTERNOON_STOP_CRON"
    log_info "US 02:30-02:50  -> $US_EARLY_START_CRON / $US_EARLY_STOP_CRON"
    log_info "US 06:30-06:50  -> $US_LATE_START_CRON / $US_LATE_STOP_CRON"
    log_info "Automatic shutdown: $AUTO_SHUTDOWN"
}

uninstall_crontab() {
    validate_environment
    require_crontab_command

    local temp_cron
    temp_cron="$(mktemp)"

    if crontab -l >/dev/null 2>&1; then
        crontab -l | strip_managed_block >"$temp_cron"

        if [ -s "$temp_cron" ]; then
            crontab "$temp_cron"
        else
            crontab -r
        fi

        rm -f "$temp_cron"
        log_success "Removed managed subscriber cron entries."
    else
        rm -f "$temp_cron"
        log_info "No crontab is currently installed."
    fi
}

show_crontab_block() {
    require_crontab_command

    if crontab -l >/dev/null 2>&1; then
        crontab -l | awk -v start="$MARKER_BEGIN" -v end="$MARKER_END" '
            $0 == start { print; show = 1; next }
            $0 == end { print; show = 0; next }
            show { print }
        '
    else
        log_info "No crontab is currently installed."
    fi
}

main() {
    local command="--install"

    while [ $# -gt 0 ]; do
        case "$1" in
            --install|--uninstall|--show|--start|--stop|--status|--help|-h)
                command="$1"
                ;;
            --interactive)
                PROMPT_MODE="always"
                ;;
            --non-interactive)
                PROMPT_MODE="never"
                ;;
            *)
                log_error "Unknown argument: $1"
                usage
                exit 1
                ;;
        esac
        shift
    done

    case "$command" in
        --install)
            if should_prompt_install; then
                configure_interactively
            fi
            install_crontab
            ;;
        --uninstall)
            uninstall_crontab
            ;;
        --show)
            show_crontab_block
            ;;
        --start)
            start_subscriber
            ;;
        --stop)
            stop_subscriber
            ;;
        --status)
            show_status
            ;;
        --help|-h)
            usage
            ;;
        *)
            log_error "Unknown command: $command"
            usage
            exit 1
            ;;
    esac
}

main "$@"
