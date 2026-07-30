import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "setup_subscriber_crontab.sh"


def _run_stop_probe(tmp_path: Path, terminal_status: int) -> subprocess.CompletedProcess[str]:
    pid_file = tmp_path / "subscriber.pid"
    pid_file.write_text("12345\n", encoding="utf-8")
    probe = f"""
source {SCRIPT!s}
PID_FILE={pid_file!s}
cleanup_stale_pid() {{ :; }}
market_open_now() {{ return 1; }}
kill() {{ :; }}
PROCESS_CALLS=0
process_running() {{
  PROCESS_CALLS=$((PROCESS_CALLS + 1))
  if [ "$PROCESS_CALLS" -eq 1 ]; then return 0; fi
  return {terminal_status}
}}
set +e
stop_subscriber KR
result=$?
set -e
printf '%s %s' "$result" "$([ -e "$PID_FILE" ] && echo present || echo absent)"
"""
    return subprocess.run(
        ["bash", "-c", probe],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )


def test_direct_cron_stop_removes_pid_after_clean_exit(tmp_path):
    result = _run_stop_probe(tmp_path, 1)
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("0 absent")


def test_direct_cron_stop_refuses_pid_reuse(tmp_path):
    result = _run_stop_probe(tmp_path, 2)
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("1 present")


def test_direct_cron_normal_exit_is_stale_and_pid_file_is_removed(tmp_path):
    pid_file = tmp_path / "subscriber.pid"
    pid_file.write_text("2147483647\n", encoding="utf-8")
    probe = f"""
source {SCRIPT!s}
PID_FILE={pid_file!s}
set +e
process_running
running_status=$?
cleanup_stale_pid
cleanup_status=$?
set -e
printf '%s %s %s' "$running_status" "$cleanup_status" "$([ -e "$PID_FILE" ] && echo present || echo absent)"
"""

    result = subprocess.run(
        ["bash", "-c", probe],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith("1 0 absent")


def test_direct_cron_market_probe_imports_trading_as_package():
    probe = f"""
source {SCRIPT!s}
PROJECT_DIR={ROOT!s}
PYTHON_PATH={sys.executable!s}
printf '%s' "$(market_open_label KR)"
"""

    result = subprocess.run(
        ["bash", "-c", probe],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout in {"yes", "no"}
    assert "attempted relative import" not in result.stderr


def test_direct_cron_explicit_action_never_enters_interactive_menu():
    probe = f"""
source {SCRIPT!s}
interactive_action_menu() {{ printf 'interactive-menu-called'; return 99; }}
validate_environment() {{ :; }}
start_subscriber() {{ printf 'start:%s' "$1"; }}
main --cron-start KR
"""

    result = subprocess.run(
        ["bash", "-c", probe],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == "start:KR"


def test_direct_cron_generated_jobs_are_non_interactive():
    probe = f"""
source {SCRIPT!s}
generate_managed_block
"""

    result = subprocess.run(
        ["bash", "-c", probe],
        cwd=ROOT,
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    cron_jobs = [
        line
        for line in result.stdout.splitlines()
        if "--cron-start" in line or "--cron-stop" in line
    ]
    assert len(cron_jobs) == 6
    assert all(line.endswith("--non-interactive") for line in cron_jobs)
