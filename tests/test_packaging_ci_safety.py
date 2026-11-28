"""Offline regressions for shipped examples and CI/package boundaries."""
from pathlib import Path
import os
import shutil
import subprocess

import pytest
import yaml

from trading.schema import parse_signal_payload
from trading.signal_safety import SignalSafetyConfig, SignalSafetyGate, REASON_QUOTE_UNAVAILABLE, OUTCOME_RETRY

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ('run.sh', 'install_prism_docker.sh', 'setup_subscriber_crontab.sh', 'setup_subscriber_docker_crontab.sh')


@pytest.mark.skipif(shutil.which('bash') is None, reason='requires bash')
@pytest.mark.parametrize('bad_script', SCRIPTS)
def test_ci_checks_each_shell_script(tmp_path, bad_script):
    workflow = yaml.safe_load((ROOT / '.github/workflows/tests.yml').read_text())
    step = next(s for s in workflow['jobs']['pytest']['steps'] if s.get('name') == 'Run static smoke checks')
    for script in SCRIPTS:
        (tmp_path / script).write_text('if then\n' if script == bad_script else 'true\n')
    bindir = tmp_path / 'bin'
    bindir.mkdir()
    python = bindir / 'python'
    python.write_text('#!/bin/sh\nexit 0\n')
    python.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ['PATH'])
    result = subprocess.run(['bash', '-e', '-c', step['run']], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode != 0, f'{bad_script} syntax error escaped CI'


def assert_dockerignore_policy(text):
    # Conservative policy, deliberately not an approximation of Docker matching:
    # exact exclusions and no overriding re-inclusions must remain present.
    rules = [line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith('#')]
    required = {'.git', '.env', 'runtime', 'logs', 'trading/config/kis_devlp.yaml', 'trading/config/KIS*', 'trading/config/.token*', 'trading/config/.tmp_token_*', 'prism-insight-*.json'}
    assert required.issubset(set(rules)), f'missing exclusions: {required - set(rules)}'
    assert not any(rule.startswith('!') for rule in rules), 're-inclusions require a Docker matcher review'
    assert '.env.example' not in rules


def test_dockerignore_excludes_sensitive_paths_without_reinclusion():
    assert_dockerignore_policy((ROOT / '.dockerignore').read_text())


@pytest.mark.parametrize('mutation', ['remove_env', 'remove_token', 'reinclude_env', 'exclude_example'])
def test_dockerignore_policy_rejects_unsafe_mutations(mutation):
    contents = (ROOT / '.dockerignore').read_text()
    if mutation == 'remove_env':
        contents = contents.replace('\n.env\n', '\n')
    elif mutation == 'remove_token':
        contents = contents.replace('trading/config/KIS*\n', '')
    elif mutation == 'reinclude_env':
        contents += '!*.env\n'
    else:
        contents += '.env.example\n'
    with pytest.raises(AssertionError):
        assert_dockerignore_policy(contents)


def test_shipped_env_defers_when_fresh_quote_is_unavailable():
    # Resolve the shipped file explicitly: the offline runner overrides the ambient
    # environment, so using os.environ here would not exercise the shipped values.
    env = dict(line.split('=', 1) for line in (ROOT / '.env.example').read_text().splitlines() if line and not line.startswith('#'))
    config = SignalSafetyConfig.resolve(env=env)
    assert config.enabled and config.fresh_quote_enabled
    class NoQuote:
        def get_current_price(self, market, ticker):
            return None
    signal = parse_signal_payload({'type': 'BUY', 'ticker': '005930', 'market': 'KR', 'price': 82000})
    result = SignalSafetyGate(config=config, quote_provider=NoQuote()).evaluate_execution(signal)
    assert (result.outcome, result.reason) == (OUTCOME_RETRY, REASON_QUOTE_UNAVAILABLE)
    assert env['WEBUI_ENABLED'] == 'false'
    assert env['WEBUI_ENABLE_LIVE_TRADING'] == 'false'
    assert env['WEBUI_HOST'] == '127.0.0.1'


def test_shipped_env_does_not_fall_back_from_missing_explicit_config(tmp_path, monkeypatch):
    from trading import config_paths
    from trading.config_paths import active_kis_config_path, implicit_example_kis_config_active
    monkeypatch.setattr(config_paths, "DEFAULT_CONFIG_PATH", tmp_path / "live.yaml")
    env = dict(line.split('=', 1) for line in (ROOT / '.env.example').read_text().splitlines() if line and not line.startswith('#'))
    assert implicit_example_kis_config_active(env)
    missing = tmp_path / 'missing.yaml'
    env['PRISM_KIS_CONFIG_PATH'] = str(missing)
    assert active_kis_config_path(env) == missing
    assert not implicit_example_kis_config_active(env)


def test_shipped_env_rejects_public_bind_without_explicit_opt_in():
    from webui.app import create_app, load_settings
    env = dict(line.split('=', 1) for line in (ROOT / '.env.example').read_text().splitlines() if line and not line.startswith('#'))
    env['WEBUI_HOST'] = '0.0.0.0'
    env['WEBUI_ALLOW_NON_LOOPBACK'] = 'invalid'
    with pytest.raises(ValueError, match='refuses non-loopback'):
        create_app(load_settings(env))
