import os
import shlex
import shutil
import subprocess
import tarfile
import textwrap
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALLER_SCRIPT = PROJECT_ROOT / "install_prism_docker.sh"


def find_bash() -> str | None:
    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        shutil.which("bash"),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


BASH_BIN = find_bash()
pytestmark = pytest.mark.skipif(BASH_BIN is None, reason="bash is required for installer smoke tests")


STUB_CURL = """#!/usr/bin/env bash
set -euo pipefail
outfile=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -o)
      shift
      outfile="$1"
      ;;
  esac
  shift || true
done
cp "$FAKE_ARCHIVE_PATH" "$outfile"
printf 'curl %s\n' "$outfile" >> "$STUB_LOG"
"""

STUB_WGET = """#!/usr/bin/env bash
set -euo pipefail
outfile=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -O)
      shift
      outfile="$1"
      ;;
  esac
  shift || true
done
cp "$FAKE_ARCHIVE_PATH" "$outfile"
printf 'wget %s\n' "$outfile" >> "$STUB_LOG"
"""

STUB_TAR = """#!/usr/bin/env bash
set -euo pipefail
printf 'tar %s\n' "$*" >> "$STUB_LOG"
exec "$REAL_TAR" "$@"
"""

STUB_DOCKER = """#!/usr/bin/env bash
set -euo pipefail
printf 'docker %s\n' "$*" >> "$STUB_LOG"
cmd="${1:-}"
shift || true
mkdir -p "$STUB_STATE_DIR"
image_file="$STUB_STATE_DIR/image"
container_file="$STUB_STATE_DIR/container"
running_file="$STUB_STATE_DIR/running"
case "$cmd" in
  image)
    if [[ "${1:-}" == "inspect" ]]; then
      [[ -f "$image_file" ]]
      exit $?
    fi
    ;;
  build)
    touch "$image_file"
    ;;
  ps)
    if [[ "${1:-}" == "-a" ]]; then
      [[ -f "$container_file" ]] && cat "$container_file"
    else
      [[ -f "$running_file" ]] && cat "$running_file"
    fi
    ;;
  rm)
    rm -f "$container_file" "$running_file"
    ;;
  create)
    name=""
    while [[ $# -gt 0 ]]; do
      if [[ "$1" == "--name" ]]; then
        shift
        name="$1"
      fi
      shift || true
    done
    printf '%s' "$name" > "$container_file"
    ;;
  start)
    printf '%s' "$1" > "$running_file"
    ;;
  stop)
    rm -f "$running_file"
    ;;
  run)
    stdin_payload="$(cat || true)"
    printf 'docker-run-stdin %s\n' "$stdin_payload" >> "$STUB_LOG"
    market=""
    for arg in "$@"; do
      if [[ "$arg" == "KR" || "$arg" == "US" ]]; then
        market="$arg"
      fi
    done
    if [[ "$market" == "KR" ]]; then
      [[ "${STUB_MARKET_OPEN_KR:-false}" == "true" ]]
      exit $?
    elif [[ "$market" == "US" ]]; then
      [[ "${STUB_MARKET_OPEN_US:-false}" == "true" ]]
      exit $?
    fi
    exit "${STUB_DOCKER_RUN_EXIT:-1}"
    ;;
esac
"""

STUB_CRONTAB = """#!/usr/bin/env bash
set -euo pipefail
printf 'crontab %s\n' "$*" >> "$STUB_LOG"
crontab_file="$STUB_STATE_DIR/crontab.txt"
if [[ "${1:-}" == "-l" ]]; then
  [[ -f "$crontab_file" ]] || exit 1
  cat "$crontab_file"
elif [[ "${1:-}" == "-r" ]]; then
  rm -f "$crontab_file"
else
  cp "$1" "$crontab_file"
fi
"""

STUB_TIMEDATECTL = """#!/usr/bin/env bash
set -euo pipefail
printf 'timedatectl %s\n' "$*" >> "$STUB_LOG"
timezone_file="$STUB_STATE_DIR/timezone.txt"
if [[ ! -f "$timezone_file" ]]; then
  printf '%s\n' "${STUB_TIMEZONE:-Asia/Seoul}" > "$timezone_file"
fi
if [[ "${1:-}" == "show" ]]; then
  cat "$timezone_file"
elif [[ "${1:-}" == "set-timezone" ]]; then
  printf '%s\n' "$2" > "$timezone_file"
fi
"""

STUB_SUDO = """#!/usr/bin/env bash
set -euo pipefail
printf 'sudo %s\n' "$*" >> "$STUB_LOG"
exec "$@"
"""

EDITOR_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
cat > "$1" <<'EOF'
default_unit_amount: 10000
default_unit_amount_usd: 100
auto_trading: true
default_mode: demo
default_product_code: "01"
my_app: "manual-real-app"
my_sec: "manual-real-secret"
paper_app: "manual-paper-app"
paper_sec: "manual-paper-secret"
my_htsid: "manual-user"
accounts:
  - name: "manual-main"
    mode: "demo"
    market: "all"
    account: "99887766"
    product: "01"
prod: "https://openapi.koreainvestment.com:9443"
ops: "ws://ops.koreainvestment.com:21000"
vps: "https://openapivts.koreainvestment.com:29443"
vops: "ws://ops.koreainvestment.com:31000"
my_token: ""
my_agent: "manual-agent"
EOF
"""


def make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(0o755)


@pytest.fixture()
def installer_env(tmp_path: Path):
    home_dir = tmp_path / "home"
    bin_dir = tmp_path / "bin"
    state_dir = tmp_path / "state"
    home_dir.mkdir()
    bin_dir.mkdir()
    state_dir.mkdir()

    make_executable(bin_dir / "curl", STUB_CURL)
    make_executable(bin_dir / "wget", STUB_WGET)
    make_executable(bin_dir / "tar", STUB_TAR)
    make_executable(bin_dir / "docker", STUB_DOCKER)
    make_executable(bin_dir / "crontab", STUB_CRONTAB)
    make_executable(bin_dir / "timedatectl", STUB_TIMEDATECTL)
    make_executable(bin_dir / "sudo", STUB_SUDO)
    make_executable(bin_dir / "kis-editor", EDITOR_SCRIPT)

    credentials_file = tmp_path / "gcp.json"
    credentials_file.write_text('{"type":"service_account"}\n', encoding="utf-8")

    archive_path = tmp_path / "prism-insight-light.tar.gz"
    root_prefix = Path("prism-insight-light-archive")
    with tarfile.open(archive_path, "w:gz") as tar:
        for relative in [
            Path("Dockerfile"),
            Path("README.md"),
            Path(".env.example"),
            Path("install_prism_docker.sh"),
            Path("setup_subscriber_docker_crontab.sh"),
            Path("subscriber.py"),
            Path("trading"),
        ]:
            tar.add(PROJECT_ROOT / relative, arcname=root_prefix / relative)

    env = os.environ.copy()
    env.update(
        {
            "HOME": home_dir.as_posix(),
            "PATH": env.get("PATH", ""),
            "STUB_BIN_DIR": bin_dir.as_posix(),
            "FAKE_ARCHIVE_PATH": archive_path.as_posix(),
            "REAL_TAR": Path(shutil.which("tar") or "/usr/bin/tar").as_posix(),
            "STUB_LOG": (state_dir / "commands.log").as_posix(),
            "STUB_STATE_DIR": state_dir.as_posix(),
            "PRISM_INSTALL_ARCHIVE_URL": f"file://{archive_path.as_posix()}",
            "PRISM_INSTALL_HOST_OS": "Linux",
            "GCP_PROJECT_ID": "demo-project",
            "GCP_PUBSUB_SUBSCRIPTION_ID": "demo-subscription",
            "GCP_CREDENTIALS_PATH": credentials_file.as_posix(),
            "DOCKER_BIN": (bin_dir / "docker").as_posix(),
            "CRONTAB_BIN": (bin_dir / "crontab").as_posix(),
            "TIMEDATECTL_BIN": (bin_dir / "timedatectl").as_posix(),
            "SUDO_BIN": (bin_dir / "sudo").as_posix(),
            "KIS_MY_APP": "real-app-key",
            "KIS_MY_SEC": "real-secret",
            "KIS_PAPER_APP": "paper-app-key",
            "KIS_PAPER_SEC": "paper-secret",
            "KIS_MY_HTSID": "demo-user",
            "KIS_ACCOUNT_NAME": "demo-account",
            "KIS_ACCOUNT_MODE": "demo",
            "KIS_ACCOUNT_MARKET": "all",
            "KIS_ACCOUNT_NUMBER": "87654321",
            "KIS_ACCOUNT_PRODUCT": "01",
            "KIS_SETUP_MODE": "guided",
            "PYTHONUTF8": "1",
        }
    )

    return {
        "tmp_path": tmp_path,
        "home_dir": home_dir,
        "bin_dir": bin_dir,
        "state_dir": state_dir,
        "credentials_file": credentials_file,
        "archive_path": archive_path,
        "env": env,
    }


def run_installer(tmp_path: Path, env: dict[str, str], *args: str, input_text: str = "") -> subprocess.CompletedProcess[str]:
    install_dir = tmp_path / "target-install"
    installer_args = ["bash", INSTALLER_SCRIPT.as_posix(), "--install-dir", install_dir.as_posix(), *args]
    shell_command = (
        f'export PATH={shlex.quote(env["STUB_BIN_DIR"])}:$PATH; '
        f"cd {shlex.quote(PROJECT_ROOT.as_posix())}; "
        + " ".join(shlex.quote(arg) for arg in installer_args)
    )
    command = [BASH_BIN, "-lc", shell_command]
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        input=input_text,
        capture_output=True,
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
    )


def run_setup_script(
    project_dir: Path,
    env: dict[str, str],
    *args: str,
    input_text: str = "",
) -> subprocess.CompletedProcess[str]:
    script_path = project_dir / "setup_subscriber_docker_crontab.sh"
    shell_command = (
        f'export PATH={shlex.quote(env["STUB_BIN_DIR"])}:$PATH; '
        f"cd {shlex.quote(PROJECT_ROOT.as_posix())}; "
        + " ".join(
            shlex.quote(arg)
            for arg in ["bash", script_path.as_posix(), *args]
        )
    )
    command = [BASH_BIN, "-lc", shell_command]
    return subprocess.run(
        command,
        text=True,
        encoding="utf-8",
        errors="replace",
        input=input_text,
        capture_output=True,
        cwd=PROJECT_ROOT,
        env=env,
        check=False,
    )

def test_install_prism_docker_cron_declined_smoke(installer_env):
    result = run_installer(installer_env["tmp_path"], installer_env["env"], "--non-interactive", "--without-cron")
    assert result.returncode == 0, result.stderr

    install_dir = installer_env["tmp_path"] / "target-install"
    env_file = install_dir / ".env"
    kis_file = install_dir / "trading" / "config" / "kis_devlp.yaml"
    log_text = (installer_env["state_dir"] / "commands.log").read_text(encoding="utf-8")

    assert env_file.exists()
    assert kis_file.exists()
    assert "GCP_PROJECT_ID=demo-project" in env_file.read_text(encoding="utf-8")
    kis_text = kis_file.read_text(encoding="utf-8")
    assert 'my_app: "real-app-key"' in kis_text
    assert 'account: "87654321"' in kis_text
    assert "docker build -t pubsub-trader ." in log_text
    assert "docker create --name prism-insight-subscriber" in log_text
    assert "crontab" not in log_text
    assert "아카이브 URL: file://" in result.stdout
    assert "cron 설치 여부: false" in result.stdout


def test_install_prism_docker_with_cron_smoke(installer_env):
    installer_env["env"]["STUB_TIMEZONE"] = "UTC"
    installer_env["env"]["AUTO_CONFIRM_TIMEZONE_CHANGE"] = "true"
    result = run_installer(
        installer_env["tmp_path"],
        installer_env["env"],
        "--non-interactive",
        "--with-cron",
    )
    assert result.returncode == 0, result.stderr

    crontab_file = installer_env["state_dir"] / "crontab.txt"
    log_text = (installer_env["state_dir"] / "commands.log").read_text(encoding="utf-8")
    assert crontab_file.exists()
    assert "BEGIN PRISM-INSIGHT SUBSCRIBER DOCKER CRON" in crontab_file.read_text(encoding="utf-8")
    assert "timedatectl show -p Timezone --value" in log_text
    assert "timedatectl set-timezone Asia/Seoul" in log_text
    assert "crontab " in log_text
    assert "현재 시스템 타임존은 'UTC' 입니다." in result.stdout


def test_install_prism_docker_accepts_kst_without_timezone_change(installer_env):
    installer_env["env"]["STUB_TIMEZONE"] = "KST"
    result = run_installer(
        installer_env["tmp_path"],
        installer_env["env"],
        "--non-interactive",
        "--with-cron",
    )
    assert result.returncode == 0, result.stderr

    log_text = (installer_env["state_dir"] / "commands.log").read_text(encoding="utf-8")
    assert "timedatectl show -p Timezone --value" in log_text
    assert "timedatectl set-timezone Asia/Seoul" not in log_text
    assert "crontab " in log_text
    assert "현재 시스템 타임존은 'KST' 입니다." not in result.stdout


def test_install_prism_docker_preserves_existing_config_on_rerun(installer_env):
    first = run_installer(installer_env["tmp_path"], installer_env["env"], "--non-interactive", "--without-cron")
    assert first.returncode == 0, first.stderr

    install_dir = installer_env["tmp_path"] / "target-install"
    env_file = install_dir / ".env"
    kis_file = install_dir / "trading" / "config" / "kis_devlp.yaml"

    env_file.write_text(
        f"GCP_PROJECT_ID=keep-project\nGCP_PUBSUB_SUBSCRIPTION_ID=keep-sub\nGCP_CREDENTIALS_PATH={installer_env['credentials_file'].as_posix()}\n",
        encoding="utf-8",
    )
    kis_file.write_text(
        textwrap.dedent(
            """
            default_mode: demo
            my_app: "keep-real"
            my_sec: "keep-secret"
            paper_app: "keep-paper"
            paper_sec: "keep-paper-secret"
            my_htsid: "keep-user"
            accounts:
              - name: "keep-account"
                mode: "demo"
                market: "all"
                account: "44556677"
                product: "01"
            prod: "https://openapi.koreainvestment.com:9443"
            ops: "ws://ops.koreainvestment.com:21000"
            vps: "https://openapivts.koreainvestment.com:29443"
            vops: "ws://ops.koreainvestment.com:31000"
            my_token: ""
            my_agent: "keep-agent"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    second_env = installer_env["env"].copy()
    second_env["GCP_PROJECT_ID"] = "new-project"
    second_env["GCP_PUBSUB_SUBSCRIPTION_ID"] = "new-sub"
    second_env["GCP_CREDENTIALS_PATH"] = installer_env["credentials_file"].as_posix()
    second_env["KIS_MY_APP"] = "new-real-app"

    second = run_installer(installer_env["tmp_path"], second_env, "--non-interactive", "--without-cron")
    assert second.returncode == 0, second.stderr
    assert "GCP_PROJECT_ID=keep-project" in env_file.read_text(encoding="utf-8")
    assert 'my_app: "keep-real"' in kis_file.read_text(encoding="utf-8")


def test_install_prism_docker_manual_kis_validation_with_editor(installer_env):
    installer_env["env"]["KIS_SETUP_MODE"] = "manual"
    installer_env["env"]["KIS_EDITOR"] = (installer_env["bin_dir"] / "kis-editor").as_posix()

    result = run_installer(installer_env["tmp_path"], installer_env["env"], "--non-interactive", "--without-cron", "--manual-kis")
    assert result.returncode == 0, result.stderr

    kis_file = installer_env["tmp_path"] / "target-install" / "trading" / "config" / "kis_devlp.yaml"
    kis_text = kis_file.read_text(encoding="utf-8")
    assert "manual-real-app" in kis_text
    assert "입력하세요" not in kis_text


def test_install_prism_docker_unsupported_platform_guard(installer_env):
    installer_env["env"]["PRISM_INSTALL_HOST_OS"] = "Darwin"
    result = run_installer(installer_env["tmp_path"], installer_env["env"], "--non-interactive", "--without-cron")
    assert result.returncode != 0
    assert "Linux 호스트용" in result.stderr
    assert not (installer_env["tmp_path"] / "target-install").exists()
    assert not (installer_env["state_dir"] / "crontab.txt").exists()


def test_setup_subscriber_docker_cron_start_respects_explicit_action(installer_env):
    result = run_installer(installer_env["tmp_path"], installer_env["env"], "--non-interactive", "--without-cron")
    assert result.returncode == 0, result.stderr

    project_dir = installer_env["tmp_path"] / "target-install"
    env = installer_env["env"].copy()
    env["PROJECT_DIR"] = project_dir.as_posix()
    env["ENV_FILE"] = (project_dir / ".env").as_posix()
    env["KIS_CONFIG_HOST_PATH"] = (project_dir / "trading" / "config" / "kis_devlp.yaml").as_posix()
    env["LOG_DIR"] = (project_dir / "logs").as_posix()
    env["RUNTIME_DIR"] = (project_dir / "runtime").as_posix()
    env["CREDENTIALS_HOST_PATH"] = env["GCP_CREDENTIALS_PATH"]
    env["STUB_MARKET_OPEN_US"] = "true"

    start_result = run_setup_script(project_dir, env, "--cron-start", "US", "--non-interactive")
    log_text = (installer_env["state_dir"] / "commands.log").read_text(encoding="utf-8")

    assert start_result.returncode == 0, start_result.stderr
    assert "subscriber Docker Crontab 메뉴" not in start_result.stdout
    assert "docker start prism-insight-subscriber" in log_text


def test_setup_subscriber_docker_prepare_runtime_resolves_relative_credentials_path(installer_env):
    result = run_installer(installer_env["tmp_path"], installer_env["env"], "--non-interactive", "--without-cron")
    assert result.returncode == 0, result.stderr

    project_dir = installer_env["tmp_path"] / "target-install"
    relative_creds = project_dir / "relative-creds.json"
    relative_creds.write_text('{"type":"service_account"}\n', encoding="utf-8")
    (project_dir / ".env").write_text(
        "GCP_PROJECT_ID=demo-project\n"
        "GCP_PUBSUB_SUBSCRIPTION_ID=demo-subscription\n"
        "GCP_CREDENTIALS_PATH=relative-creds.json\n",
        encoding="utf-8",
    )
    (installer_env["state_dir"] / "commands.log").write_text("", encoding="utf-8")

    env = installer_env["env"].copy()
    env["PROJECT_DIR"] = project_dir.as_posix()
    env["ENV_FILE"] = (project_dir / ".env").as_posix()
    env["KIS_CONFIG_HOST_PATH"] = (project_dir / "trading" / "config" / "kis_devlp.yaml").as_posix()
    env["LOG_DIR"] = (project_dir / "logs").as_posix()
    env["RUNTIME_DIR"] = (project_dir / "runtime").as_posix()
    env.pop("CREDENTIALS_HOST_PATH", None)

    prepare_result = run_setup_script(project_dir, env, "--prepare-runtime", "--non-interactive")
    log_text = (installer_env["state_dir"] / "commands.log").read_text(encoding="utf-8")

    assert prepare_result.returncode == 0, prepare_result.stderr
    assert f"-v {relative_creds.as_posix()}:/app/runtime/gcp-credentials.json:ro" in log_text


def test_setup_subscriber_docker_status_uses_interactive_python_market_check(installer_env):
    result = run_installer(installer_env["tmp_path"], installer_env["env"], "--non-interactive", "--without-cron")
    assert result.returncode == 0, result.stderr

    project_dir = installer_env["tmp_path"] / "target-install"
    env = installer_env["env"].copy()
    env["PROJECT_DIR"] = project_dir.as_posix()
    env["ENV_FILE"] = (project_dir / ".env").as_posix()
    env["KIS_CONFIG_HOST_PATH"] = (project_dir / "trading" / "config" / "kis_devlp.yaml").as_posix()
    env["LOG_DIR"] = (project_dir / "logs").as_posix()
    env["RUNTIME_DIR"] = (project_dir / "runtime").as_posix()
    env["CREDENTIALS_HOST_PATH"] = env["GCP_CREDENTIALS_PATH"]
    env["STUB_MARKET_OPEN_KR"] = "false"
    env["STUB_MARKET_OPEN_US"] = "true"

    status_result = run_setup_script(project_dir, env, "--status", "--non-interactive")
    log_text = (installer_env["state_dir"] / "commands.log").read_text(encoding="utf-8")

    assert status_result.returncode == 0, status_result.stderr
    assert "KR open         : no" in status_result.stdout
    assert "US open         : yes" in status_result.stdout
    assert "docker run --rm -i --entrypoint python" in log_text
