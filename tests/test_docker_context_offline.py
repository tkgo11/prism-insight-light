"""Inspect Docker CLI's context tar through a fake local API, never a daemon."""
import http.server
import io
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import tarfile
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]


class FakeDocker(socketserver.ThreadingMixIn, getattr(socketserver, "UnixStreamServer", socketserver.TCPServer)):
    daemon_threads = True


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_HEAD(self):
        if self.path != '/_ping':
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('API-Version', '1.41')
        self.end_headers()

    def do_POST(self):
        if not self.path.startswith('/v1.41/build?'):
            self.send_error(404)
            return
        # The legacy Docker CLI sends the context as chunked transfer encoding.
        data = bytearray()
        while True:
            size = int(self.rfile.readline().split(b';', 1)[0].strip(), 16)
            if not size:
                self.rfile.readline()
                break
            data.extend(self.rfile.read(size))
            assert self.rfile.read(2) == b'\r\n'
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            self.server.names = set(archive.getnames())
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(b'{"stream":"context captured\\n"}\n')


@pytest.mark.skipif(os.name == 'nt' or not hasattr(socketserver, 'UnixStreamServer') or not shutil.which('docker'), reason='requires Docker CLI and Unix sockets')
@pytest.mark.parametrize('mutation, env_in_context', [('none', False), ('remove', True), ('reinclude', True)])
def test_actual_docker_client_context_exclusions(tmp_path, mutation, env_in_context):
    context = tmp_path / 'context'
    context.mkdir()
    rules = (ROOT / '.dockerignore').read_text()
    if mutation == 'remove':
        rules = rules.replace('\n.env\n', '\n')
    elif mutation == 'reinclude':
        rules += '\n!.env\n'
    (context / '.dockerignore').write_text(rules)
    # All Dockerfile COPY inputs plus representative secret/state paths.
    for path in ('Dockerfile', 'requirements.txt', 'subscriber.py', 'pubsub_readiness.py',
                 'check_pubsub_readiness.py', '.env.example', '.env',
                 'trading/config/kis_devlp.yaml', 'trading/config/KIS_test.token',
                 'trading/config/.token_cache', 'trading/config/.tmp_token_test',
                 'trading/config/kis_devlp.yaml.example', 'trading/__init__.py',
                 'webui/__init__.py', 'runtime/ledger.json', 'logs/log.txt',
                 'prism-insight-secret.json'):
        target = context / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('FROM scratch\n' if path == 'Dockerfile' else 'fixture\n')
    socket = str(tmp_path / 'docker.sock')
    with FakeDocker(socket, Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Do not inherit credentials or the user's Docker contexts/config.
            config_dir = tmp_path / 'docker-config'
            config_dir.mkdir()
            env = {'PATH': os.environ.get('PATH', ''), 'DOCKER_CONFIG': str(config_dir),
                   'DOCKER_HOST': 'unix://' + socket, 'DOCKER_BUILDKIT': '0',
                   'DOCKER_API_VERSION': '1.41'}
            result = subprocess.run(['docker', 'build', str(context)], env=env, capture_output=True, text=True, timeout=20)
            assert result.returncode == 0, result.stderr
            names = server.names
        finally:
            server.shutdown()
            thread.join()
    assert ('.env' in names) is env_in_context
    assert {'.env.example', 'requirements.txt', 'subscriber.py', 'pubsub_readiness.py',
            'check_pubsub_readiness.py', 'trading/config/kis_devlp.yaml.example',
            'trading/__init__.py', 'webui/__init__.py'} <= names
    assert not {'trading/config/kis_devlp.yaml', 'trading/config/KIS_test.token',
                'trading/config/.token_cache', 'trading/config/.tmp_token_test',
                'runtime/ledger.json', 'logs/log.txt', 'prism-insight-secret.json'} & names
