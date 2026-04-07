import atexit
import sys
import textwrap
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

CONFIG_DIR = PROJECT_ROOT / "trading" / "config"
CONFIG_FILE = CONFIG_DIR / "kis_devlp.yaml"
_CREATED_TEST_CONFIG = False

if not CONFIG_FILE.exists():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
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
    _CREATED_TEST_CONFIG = True

if _CREATED_TEST_CONFIG:
    atexit.register(lambda: CONFIG_FILE.unlink(missing_ok=True))
