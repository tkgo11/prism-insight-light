"""The update helper must preserve work and stop before launch on failure."""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'run.sh'


@pytest.mark.parametrize('failure', ['dirty', 'pull', 'install', ''])
def test_run_script_preserves_work_and_stops_on_failure(tmp_path, failure):
    # Copy the script into a disposable project. Executables only record calls;
    # this never fetches a remote, installs packages, or starts the subscriber.
    project = tmp_path / 'project'
    project.mkdir()
    script = project / 'run.sh'
    script.write_bytes(SCRIPT.read_bytes())
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    log = tmp_path / 'commands'
    for executable in ['git', 'python', 'pip']:
        fake = binaries / executable
        fake.write_text('''#!/bin/bash
printf '%s\\n' "$(basename "$0") $*" >> "$COMMAND_LOG"
case "$1" in
 status) if [[ "$FAILURE" == dirty ]]; then echo ' M subscriber.py'; fi ;;
 pull) [[ "$FAILURE" != pull ]] || exit 7 ;;
esac
if [[ "$1 $2" == '-m pip' && "$FAILURE" == install ]]; then exit 8; fi
exit 0
''')
        fake.chmod(0o755)
    result = subprocess.run(
        ['bash', str(script), '--dry-run'], cwd=tmp_path,
        env={**os.environ, 'PATH': f'{binaries}:{os.environ["PATH"]}',
             'COMMAND_LOG': str(log), 'FAILURE': failure},
        capture_output=True, text=True,
    )
    calls = log.read_text()
    assert 'reset' not in calls
    assert (result.returncode == 0) == (failure == '')
    if failure:
        assert 'python subscriber.py' not in calls
    else:
        assert 'git pull --ff-only' in calls
        assert 'python -m pip install -r requirements.txt' in calls
        assert 'python subscriber.py --dry-run' in calls
