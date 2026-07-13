import os
import subprocess
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
