import os
import sys
import tempfile
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

_TEST_CONFIG_DIR = tempfile.TemporaryDirectory(prefix="prism-insight-light-tests-")
CONFIG_FILE = Path(_TEST_CONFIG_DIR.name) / "kis_devlp.yaml"
os.environ["PRISM_KIS_CONFIG_PATH"] = str(CONFIG_FILE)
CONFIG_FILE.write_text(
    textwrap.dedent(
            """
            my_agent: test-agent
            default_mode: demo
            auto_trading: true
            default_product_code: "01"
            default_unit_amount: 100000
            default_unit_amount_usd: 250
            my_app: PSREALKEY
            my_sec: real-secret
            paper_app: PSVTTESTKEY
            paper_sec: paper-secret
            my_htsid: test-user
            prod: https://example.com
            vps: https://example.com
            ops: wss://example.com
            vops: wss://example.com
            accounts:
              - name: bootstrap-demo-kr
                mode: demo
                account: "12345678"
                product: "01"
                market: kr
                primary: true
              - name: bootstrap-demo-us
                mode: demo
                account: "87654321"
                product: "01"
                market: us
                primary: true
            """
    ).strip()
    + "\n",
    encoding="utf-8",
)


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: run async tests without requiring pytest-asyncio")


def pytest_pyfunc_call(pyfuncitem):
    if "asyncio" not in pyfuncitem.keywords:
        return None

    import asyncio
    import inspect

    testfunction = pyfuncitem.obj
    if not inspect.iscoroutinefunction(testfunction):
        return None

    fixture_args = {name: pyfuncitem.funcargs[name] for name in pyfuncitem._fixtureinfo.argnames}
    asyncio.run(testfunction(**fixture_args))
    return True
