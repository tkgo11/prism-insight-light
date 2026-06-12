import pytest

from trading import kis_auth as ka


def _patch_cfg(monkeypatch, cfg):
    monkeypatch.setattr(ka, "_cfg", cfg, raising=False)
    monkeypatch.setattr(
        ka,
        "DEFAULT_PRODUCT_CODE",
        str(cfg.get("default_product_code", cfg.get("my_prod", "01"))),
        raising=False,
    )
    monkeypatch.setattr(
        ka,
        "DEFAULT_BUY_AMOUNT_KRW",
        int(cfg.get("default_unit_amount", 0) or 0),
        raising=False,
    )
    monkeypatch.setattr(
        ka,
        "DEFAULT_BUY_AMOUNT_USD",
        float(cfg.get("default_unit_amount_usd", 0) or 0),
        raising=False,
    )
    monkeypatch.setattr(
        ka,
        "DEFAULT_BUY_PERCENT_KRW",
        ka.normalize_percent(cfg.get("default_unit_asset_percent")),
        raising=False,
    )
    monkeypatch.setattr(
        ka,
        "DEFAULT_BUY_PERCENT_USD",
        ka.normalize_percent(cfg.get("default_unit_asset_percent_usd")),
        raising=False,
    )


def _base_cfg():
    return {
        "default_product_code": "01",
        "default_unit_amount": 100000,
        "default_unit_amount_usd": 250.0,
        "my_prod": "01",
    }


def test_get_configured_accounts_normalizes_filters_and_preserves_overrides(monkeypatch):
    cfg = _base_cfg()
    cfg["accounts"] = [
        {
            "name": "kr-demo",
            "mode": "demo",
            "account": "11112222",
            "product": "01",
            "market": "kr",
            "buy_amount_krw": 77777,
        },
        {
            "name": "us-demo-primary",
            "mode": "demo",
            "account": "33334444",
            "product": "01",
            "market": "us",
            "primary": True,
            "buy_amount_usd": 123.45,
        },
        {
            "name": "us-real",
            "mode": "real",
            "account": "55556666",
            "product": "01",
            "market": "us",
        },
    ]
    _patch_cfg(monkeypatch, cfg)

    us_demo_accounts = ka.get_configured_accounts(svr="demo", market="us")

    assert [account["name"] for account in us_demo_accounts] == ["us-demo-primary"]
    assert us_demo_accounts[0]["svr"] == "vps"
    assert us_demo_accounts[0]["account_key"] == "vps:33334444:01"
    assert us_demo_accounts[0]["primary"] is True
    assert us_demo_accounts[0]["buy_amount_usd"] == 123.45
    assert us_demo_accounts[0]["buy_percent_usd"] is None


def test_get_configured_accounts_does_not_cross_market_fallback(monkeypatch):
    cfg = _base_cfg()
    cfg["accounts"] = [
        {
            "name": "kr-demo",
            "mode": "demo",
            "account": "11112222",
            "product": "01",
            "market": "kr",
        }
    ]
    _patch_cfg(monkeypatch, cfg)

    assert ka.get_configured_accounts(svr="demo", market="us") == []


def test_get_configured_accounts_supports_legacy_fallback(monkeypatch):
    cfg = _base_cfg()
    cfg.update(
        {
            "my_acct_stock": "87654321",
            "my_paper_stock": "12345678",
            "my_prod": "01",
        }
    )
    _patch_cfg(monkeypatch, cfg)

    with pytest.warns(DeprecationWarning):
        accounts = ka.get_configured_accounts()

    assert [account["name"] for account in accounts] == [
        "legacy-real-stock",
        "legacy-demo-stock",
    ]
    assert accounts[0]["market"] == "all"
    assert accounts[0]["primary"] is True
    assert accounts[0]["buy_amount_krw"] == 100000
    assert accounts[0]["buy_amount_usd"] == 250.0
    assert accounts[0]["buy_percent_krw"] is None
    assert accounts[0]["buy_percent_usd"] is None
    assert accounts[1]["account_key"] == "vps:12345678:01"


def test_get_configured_accounts_preserves_percent_sizing(monkeypatch):
    cfg = _base_cfg()
    cfg.update({"default_unit_asset_percent": 1.5, "default_unit_asset_percent_usd": 2.5})
    cfg["accounts"] = [
        {
            "name": "kr-demo",
            "mode": "demo",
            "account": "11112222",
            "product": "01",
            "market": "kr",
            "buy_percent_krw": 3.0,
        },
        {
            "name": "us-demo",
            "mode": "demo",
            "account": "33334444",
            "product": "01",
            "market": "us",
        },
    ]
    _patch_cfg(monkeypatch, cfg)

    kr_account = ka.get_configured_accounts(svr="demo", market="kr")[0]
    us_account = ka.get_configured_accounts(svr="demo", market="us")[0]

    assert kr_account["buy_percent_krw"] == 3.0
    assert kr_account["buy_percent_usd"] == 2.5
    assert us_account["buy_percent_krw"] == 1.5
    assert us_account["buy_percent_usd"] == 2.5


def test_resolve_account_prefers_primary_then_first(monkeypatch):
    cfg = _base_cfg()
    cfg["accounts"] = [
        {
            "name": "first-us",
            "mode": "demo",
            "account": "10000001",
            "product": "01",
            "market": "us",
        },
        {
            "name": "primary-us",
            "mode": "demo",
            "account": "10000002",
            "product": "01",
            "market": "us",
            "primary": True,
        },
    ]
    _patch_cfg(monkeypatch, cfg)

    primary = ka.resolve_account(svr="vps", market="us")
    assert primary["name"] == "primary-us"

    cfg["accounts"][1]["primary"] = False
    _patch_cfg(monkeypatch, cfg)

    fallback = ka.resolve_account(svr="vps", market="us")
    assert fallback["name"] == "first-us"


def test_resolve_account_supports_account_key(monkeypatch):
    cfg = _base_cfg()
    cfg["accounts"] = [
        {
            "name": "acct-a",
            "mode": "demo",
            "account": "11110000",
            "product": "01",
            "market": "kr",
        },
        {
            "name": "acct-b",
            "mode": "demo",
            "account": "22220000",
            "product": "01",
            "market": "kr",
        },
    ]
    _patch_cfg(monkeypatch, cfg)

    resolved = ka.resolve_account(
        svr="vps",
        product="01",
        account_key="vps:22220000:01",
        market="kr",
    )

    assert resolved["name"] == "acct-b"
    assert resolved["account"] == "22220000"


def test_get_configured_accounts_rejects_unknown_mode(monkeypatch):
    _patch_cfg(monkeypatch, _base_cfg())

    with pytest.raises(ValueError, match="Unknown server mode"):
        ka.get_configured_accounts(svr="staging")


def test_get_configured_accounts_rejects_too_many_accounts(monkeypatch):
    cfg = _base_cfg()
    cfg["accounts"] = [
        {
            "name": f"acct-{index}",
            "mode": "demo",
            "account": f"{10000000 + index}",
            "product": "01",
            "market": "kr",
        }
        for index in range(11)
    ]
    _patch_cfg(monkeypatch, cfg)

    with pytest.raises(ValueError, match="Too many accounts configured"):
        ka.get_configured_accounts()

class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = payload if isinstance(payload, str) else __import__("json").dumps(payload, ensure_ascii=False)
        self.headers = {}

    def json(self):
        if isinstance(self._payload, str):
            return __import__("json").loads(self._payload)
        return self._payload


def test_url_fetch_retries_kis_rate_limit_then_succeeds(monkeypatch):
    calls = []
    sleeps = []

    class Env:
        my_url = "https://example.com"

    monkeypatch.setattr(ka, "getTREnv", lambda: Env())
    monkeypatch.setattr(ka, "_getBaseHeader", lambda: {})
    monkeypatch.setattr(ka, "isPaperTrading", lambda: False)
    monkeypatch.setattr(ka, "KIS_RATE_LIMIT_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr(ka, "KIS_RATE_LIMIT_RETRY_BASE_SECONDS", 0.25)
    monkeypatch.setattr(ka, "KIS_RATE_LIMIT_RETRY_MAX_SECONDS", 1.0)
    monkeypatch.setattr(ka.time, "sleep", lambda seconds: sleeps.append(seconds))

    def fake_post(url, headers, data):
        calls.append((url, headers, data))
        if len(calls) == 1:
            return _FakeResponse(
                500,
                {
                    "rt_cd": "1",
                    "msg_cd": "EGW00201",
                    "msg1": "원장에서 허용 가능한 초당 거래건수를 초과하였습니다.",
                },
            )
        return _FakeResponse(200, {"rt_cd": "0", "msg_cd": "0", "msg1": "OK", "output": {}})

    monkeypatch.setattr(ka.requests, "post", fake_post)

    response = ka._url_fetch("/uapi/test", "TTTC0012U", "", {"PDNO": "085620"}, postFlag=True)

    assert response.isOK()
    assert len(calls) == 2
    assert sleeps == [0.25]


def test_url_fetch_stops_after_configured_rate_limit_retries(monkeypatch):
    calls = []
    sleeps = []

    class Env:
        my_url = "https://example.com"

    monkeypatch.setattr(ka, "getTREnv", lambda: Env())
    monkeypatch.setattr(ka, "_getBaseHeader", lambda: {})
    monkeypatch.setattr(ka, "isPaperTrading", lambda: False)
    monkeypatch.setattr(ka, "KIS_RATE_LIMIT_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(ka, "KIS_RATE_LIMIT_RETRY_BASE_SECONDS", 0.5)
    monkeypatch.setattr(ka, "KIS_RATE_LIMIT_RETRY_MAX_SECONDS", 1.0)
    monkeypatch.setattr(ka.time, "sleep", lambda seconds: sleeps.append(seconds))

    def fake_get(url, headers, params):
        calls.append((url, headers, params))
        return _FakeResponse(
            500,
            {
                "rt_cd": "1",
                "msg_cd": "EGW00201",
                "msg1": "원장에서 허용 가능한 초당 거래건수를 초과하였습니다.",
            },
        )

    monkeypatch.setattr(ka.requests, "get", fake_get)

    response = ka._url_fetch("/uapi/test", "FHKST01010100", "", {"fid_input_iscd": "085620"})

    assert not response.isOK()
    assert response.getErrorCode() == "EGW00201"
    assert len(calls) == 2
    assert sleeps == [0.5]


def test_url_fetch_does_not_retry_non_rate_limit_errors(monkeypatch):
    calls = []

    class Env:
        my_url = "https://example.com"

    monkeypatch.setattr(ka, "getTREnv", lambda: Env())
    monkeypatch.setattr(ka, "_getBaseHeader", lambda: {})
    monkeypatch.setattr(ka, "isPaperTrading", lambda: False)
    monkeypatch.setattr(ka, "KIS_RATE_LIMIT_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr(ka.time, "sleep", lambda seconds: pytest.fail("unexpected retry sleep"))

    def fake_get(url, headers, params):
        calls.append((url, headers, params))
        return _FakeResponse(500, {"rt_cd": "1", "msg_cd": "OTHER", "msg1": "other failure"})

    monkeypatch.setattr(ka.requests, "get", fake_get)

    response = ka._url_fetch("/uapi/test", "FHKST01010100", "", {})

    assert not response.isOK()
    assert response.getErrorCode() == "OTHER"
    assert len(calls) == 1


def test_token_expiry_timestamp_is_interpreted_as_korean_local_time():
    valid_date = ka.datetime.strptime("2026-06-12 09:51:28", "%Y-%m-%d %H:%M:%S")
    now_utc = ka.datetime(2026, 6, 12, 1, 2, 3, tzinfo=ka.ZoneInfo("UTC"))

    assert ka._is_token_expired(valid_date, now=now_utc)


def test_url_fetch_refreshes_expired_token_once(monkeypatch):
    calls = []
    refreshes = []

    class Env:
        my_url = "https://example.com"

    monkeypatch.setattr(ka, "getTREnv", lambda: Env())
    monkeypatch.setattr(ka, "_getBaseHeader", lambda: {"authorization": f"Bearer token-{len(refreshes)}"})
    monkeypatch.setattr(ka, "isPaperTrading", lambda: False)
    monkeypatch.setattr(ka, "_refresh_after_expired_token_response", lambda: refreshes.append("refresh"))
    monkeypatch.setattr(ka, "KIS_RATE_LIMIT_RETRY_ATTEMPTS", 3)

    def fake_get(url, headers, params):
        calls.append((url, dict(headers), params))
        if len(calls) == 1:
            return _FakeResponse(
                500,
                {
                    "rt_cd": "1",
                    "msg_cd": "EGW00123",
                    "msg1": "기간이 만료된 token 입니다.",
                },
            )
        return _FakeResponse(200, {"rt_cd": "0", "msg_cd": "0", "msg1": "OK", "output": {}})

    monkeypatch.setattr(ka.requests, "get", fake_get)

    response = ka._url_fetch("/uapi/test", "TTTC8434R", "", {"CANO": "12345678"})

    assert response.isOK()
    assert len(calls) == 2
    assert refreshes == ["refresh"]
    assert calls[0][1]["authorization"] == "Bearer token-0"
    assert calls[1][1]["authorization"] == "Bearer token-1"
