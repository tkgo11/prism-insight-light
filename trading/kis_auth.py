"""
KIS API Authentication, HTTP helpers, and WebSocket support.
Refactored to use KISAuthManager for better state management.
"""

import asyncio
import copy
import json
import logging
import os
import stat
import time
from base64 import b64decode, b64encode
from collections import namedtuple
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import Callable, Optional, Tuple, Dict, Any

import pandas as pd
import requests
import websockets
import yaml
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
from cryptography.fernet import Fernet
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential
import aiohttp
from trading.rate_limiter import KISRateLimiter

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class KISAuthError(Exception):
    pass

class TokenFileError(KISAuthError):
    pass

class CredentialMismatchError(KISAuthError):
    pass

class TokenRequestError(KISAuthError):
    def __init__(self, message, status_code=None, response_text=None):
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text

# ---------------------------------------------------------------------------
# File security helpers
# ---------------------------------------------------------------------------

class CrossPlatformFileLock:
    """File lock using atomic file creation."""

    def __init__(self, lock_path: str, timeout: float = 30.0):
        self.lock_path = lock_path
        self.timeout = timeout
        self.acquired = False

    def acquire(self) -> bool:
        deadline = time.time() + self.timeout
        while time.time() < deadline:
            try:
                fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                self.acquired = True
                return True
            except FileExistsError:
                time.sleep(0.1)
        return False

    def release(self):
        if self.acquired:
            try:
                os.unlink(self.lock_path)
            except OSError:
                pass
            self.acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()

def _set_file_permissions(file_path: str):
    """Restrict file to owner read/write only."""
    try:
        os.chmod(file_path, stat.S_IRUSR | stat.S_IWUSR)
    except Exception:
        pass

def _atomic_file_write(file_path: str, data: str):
    """Write file atomically via tmp + rename."""
    tmp = file_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data)
    os.replace(tmp, file_path)
    _set_file_permissions(file_path)

# ---------------------------------------------------------------------------
# Token encryption
# ---------------------------------------------------------------------------

def _encrypt_token(token: str) -> str:
    key = Fernet.generate_key()
    encrypted = Fernet(key).encrypt(token.encode()).decode()
    return json.dumps({"data": encrypted, "key": key.decode()})

def _decrypt_token(encrypted_data: str) -> str:
    d = json.loads(encrypted_data)
    return Fernet(d["key"].encode()).decrypt(d["data"].encode()).decode()

# ---------------------------------------------------------------------------
# API response classes
# ---------------------------------------------------------------------------

class APIResp:
    def __init__(self, resp):
        self._rescode = resp.status_code
        self._resp = resp
        self._header = self._setHeader()
        self._body = self._setBody()
        self._err_code = self._body.msg_cd
        self._err_message = self._body.msg1

    def _setHeader(self):
        fld = {x: self._resp.headers.get(x) for x in self._resp.headers.keys() if x.islower()}
        return namedtuple("header", fld.keys())(**fld)

    def _setBody(self):
        return namedtuple("body", self._resp.json().keys())(**self._resp.json())

    def getResCode(self):
        return self._rescode

    def getHeader(self):
        return self._header

    def getBody(self):
        return self._body

    def getResponse(self):
        return self._resp

    def isOK(self):
        try:
            return self.getBody().rt_cd == "0"
        except Exception:
            return False

    def getErrorCode(self):
        return self._err_code

    def getErrorMessage(self):
        return self._err_message

    def printAll(self):
        print("<Header>")
        for x in self.getHeader()._fields:
            print(f"\t-{x}: {getattr(self.getHeader(), x)}")
        print("<Body>")
        for x in self.getBody()._fields:
            print(f"\t-{x}: {getattr(self.getBody(), x)}")

    def printError(self, url):
        print(f"Error: {self.getResCode()} url={url}")
        print(f"rt_cd: {self.getBody().rt_cd} / msg_cd: {self.getErrorCode()} / msg1: {self.getErrorMessage()}")

class APIRespError(APIResp):
    def __init__(self, status_code, error_text):
        self.status_code = status_code
        self.error_text = error_text
        self._error_code = str(status_code)
        self._error_message = error_text

    def isOK(self):
        return False

    def getErrorCode(self):
        return self._error_code

    def getErrorMessage(self):
        return self._error_message

    def getBody(self):
        class _E:
            def __getattr__(self, _):
                return None
        return _E()

    def getHeader(self):
        class _E:
            tr_cont = ""
            def __getattr__(self, _):
                return ""
        return _E()

    def printAll(self):
        print(f"ERROR: {self.status_code} - {self.error_text}")

    def printError(self, url=""):
        print(f"Error Code: {self.status_code} | {self.error_text}")
        if url:
            print(f"URL: {url}")

# ---------------------------------------------------------------------------
# Auth Manager
# ---------------------------------------------------------------------------

TREnv = namedtuple("TREnv", ["my_app", "my_sec", "my_acct", "my_prod", "my_token", "my_url", "my_url_ws"])

class KISAuthManager:
    """
    Manages authentication and API requests for KIS.
    Replaces global state with instance-based management.
    """
    _TOKEN_PATH = Path(__file__).parent / "token.dat"
    _CFG_PATH = Path(__file__).parent / "config" / "kis_devlp.yaml" # Try new config location
    
    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path
        if not self.config_path or not self.config_path.exists():
             # Fallback to old location if not provided
             self.config_path = Path(__file__).parent / "kis_devlp.yaml"

        self._cfg = self._load_config()
        self._debug = self._cfg.get("debug", False)
        self._auto_reauth = True
        self._smart_sleep_time = self._cfg.get("rate_limit_sleep", 0.1)
        
        self.rate_limiter = KISRateLimiter()
        self._session: Optional[aiohttp.ClientSession] = None
        
        self.train_env: Optional[TREnv] = None
        self.base_headers = {"content-type": "application/json; charset=utf-8", "Accept": "text/plain"}
        self.last_auth_time = datetime.now()

    def _load_config(self) -> Dict[str, Any]:
        if self.config_path.exists():
            with open(self.config_path, encoding="utf-8") as f:
                return yaml.safe_load(f)
        return {}
    
    def get_config(self) -> Dict[str, Any]:
        return self._cfg

    def smart_sleep(self):
        if self._debug:
            print(f"[RateLimit] Sleeping {self._smart_sleep_time}s")
        time.sleep(self._smart_sleep_time)

    def is_paper_trading(self) -> bool:
        return self.train_env is not None and "vps" in (self.train_env.my_url or "")

    def get_tr_env(self) -> TREnv:
        if self.train_env is None:
            raise RuntimeError("Call auth() first.")
        return self.train_env

    def validate_credentials(self, app_key: str, mode: str) -> Tuple[bool, str]:
        """Check app_key matches trading mode (real vs demo)."""
        if mode == "real" and app_key.startswith("PSVT"):
            return False, "Real mode requires PS* key, not PSVT* (paper) key"
        if mode != "real" and app_key.startswith("PS") and not app_key.startswith("PSVT"):
            return False, "Demo mode requires PSVT* key, not PS* (real) key"
        return True, "OK"

    def read_token(self) -> Optional[str]:
        """Read and decrypt cached token, return None if expired/missing."""
        if not self._TOKEN_PATH.exists():
            return None
        lock = CrossPlatformFileLock(str(self._TOKEN_PATH) + ".lock")
        try:
            lock.acquire()
            raw = self._TOKEN_PATH.read_text(encoding="utf-8")
            data = json.loads(raw)
            expires = data.get("expires", "")
            if expires:
                if datetime.now() >= datetime.strptime(expires, "%Y-%m-%d %H:%M:%S"):
                    self._TOKEN_PATH.unlink(missing_ok=True)
                    return None
            try:
                return _decrypt_token(data["token"])
            except Exception:
                return data.get("token")  # Legacy unencrypted format
        except Exception:
            return None
        finally:
            lock.release()

    def save_token(self, token: str, expires: str):
        """Save encrypted token to disk."""
        lock = CrossPlatformFileLock(str(self._TOKEN_PATH) + ".lock")
        try:
            lock.acquire()
            encrypted = _encrypt_token(token)
            data = json.dumps({"token": encrypted, "expires": expires})
            _atomic_file_write(str(self._TOKEN_PATH), data)
        finally:
            lock.release()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=5, max=60),
        retry=retry_if_exception_type((requests.RequestException,)),
        reraise=True,
    )
    def _request_token_with_retry(self, url: str, params: dict, headers: dict) -> dict:
        resp = requests.post(url, data=json.dumps(params), headers=headers)
        if resp.status_code == 200:
            return resp.json()
        raise TokenRequestError(f"Token request failed: {resp.status_code}",
                                status_code=resp.status_code, response_text=resp.text)

    def _get_base_header(self):
        if self._auto_reauth:
             # Logic to check expiry and re-auth effectively handled by checking last_auth_time in auth/reAuth wrappers
             # but here we just return headers. 
             # For simplicity, reAuth check is done before calling this or inside high level methods.
             pass
        return copy.deepcopy(self.base_headers)

    def auth(self, svr: str = "prod", product: str = None, url: str = None):
        """Authenticate with KIS API."""
        product = product or self._cfg.get("my_prod", "01")

        if svr == "prod":
            app_key = self._cfg.get("my_app")
            app_secret = self._cfg.get("my_sec")
            base_url = url or self._cfg.get("prod")
        else:
            app_key = self._cfg.get("paper_app")
            app_secret = self._cfg.get("paper_sec")
            base_url = url or self._cfg.get("vps")
            
        if not app_key or not app_secret:
             raise KISAuthError(f"Missing credentials for {svr} mode in config")

        mode = "real" if svr == "prod" else "demo"
        valid, msg = self.validate_credentials(app_key, mode)
        if not valid:
            raise CredentialMismatchError(msg)

        saved_token = self.read_token()
        my_token = None
        
        if not saved_token:
            token_url = f"{base_url}/oauth2/tokenP"
            p = {"grant_type": "client_credentials", "appkey": app_key, "appsecret": app_secret}
            try:
                result = self._request_token_with_retry(token_url, p, self._get_base_header())
                my_token = result.get("access_token")
                my_expired = result.get("access_token_token_expired")
                if not my_token or not my_expired:
                    raise TokenRequestError("Missing token or expiry", status_code=200, response_text=str(result))
                self.save_token(my_token, my_expired)
                logging.info(f"New token saved (expires: {my_expired})")
            except TokenRequestError:
                raise
            except Exception as e:
                raise TokenRequestError(f"Unexpected error: {e}")
        else:
            my_token = saved_token
            logging.info("Using cached token")

        # Update Environment
        acct = self._cfg.get("my_acct", "")
        ws_url = base_url.replace("https://", "wss://")
        self.train_env = TREnv(app_key, app_secret, acct, product, my_token, base_url, ws_url)
        
        self.base_headers["authorization"] = f"Bearer {my_token}"
        self.base_headers["appkey"] = app_key
        self.base_headers["appsecret"] = app_secret

        self.last_auth_time = datetime.now()
        if self._debug:
            print(f"[{self.last_auth_time}] => AUTH completed")

    def re_auth(self, svr="prod", product=None):
        """Re-authenticate if token is older than 23 hours."""
        if (datetime.now() - self.last_auth_time).total_seconds() >= 82800:
            logging.info("Token approaching expiry, re-authenticating...")
            self.auth(svr, product)

    def url_fetch(self, api_url, ptr_id, tr_cont, params, appendHeaders=None, postFlag=False, hashFlag=True) -> APIResp:
        # Check re-auth
        self.re_auth()
        
        env = self.get_tr_env()
        url = f"{env.my_url}{api_url}"
        headers = self._get_base_header()

        tr_id = ptr_id
        if ptr_id[0] in ("T", "J", "C") and self.is_paper_trading():
            tr_id = "V" + ptr_id[1:]

        headers["tr_id"] = tr_id
        headers["custtype"] = "P"
        headers["tr_cont"] = tr_cont

        if appendHeaders:
            headers.update(appendHeaders)

        if self._debug:
            print(f"URL: {url}, TR: {tr_id}\n<header>\n{headers}\n<body>\n{params}")

        if postFlag:
            res = requests.post(url, headers=headers, data=json.dumps(params))
        else:
            res = requests.get(url, headers=headers, params=params)

        if res.status_code == 200:
            ar = APIResp(res)
            if self._debug:
                ar.printAll()
            return ar
        else:
            print(f"Error Code: {res.status_code} | {res.text}")
            return APIRespError(res.status_code, res.text)

    async def get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def async_url_fetch(self, api_url: str, ptr_id: str, tr_cont: str, params: dict, 
                              appendHeaders: dict = None, postFlag: bool = False, 
                              market: str = "KR") -> APIResp:
        """Asynchronous version of url_fetch with rate limiting."""
        # 1. Rate limit wait
        await self.rate_limiter.wait_for(market)

        # 2. Check re-auth (sync for now, or could be async-ified)
        self.re_auth()
        
        env = self.get_tr_env()
        url = f"{env.my_url}{api_url}"
        headers = self._get_base_header()

        tr_id = ptr_id
        if ptr_id[0] in ("T", "J", "C") and self.is_paper_trading():
            tr_id = "V" + ptr_id[1:]

        headers["tr_id"] = tr_id
        headers["custtype"] = "P"
        headers["tr_cont"] = tr_cont

        if appendHeaders:
            headers.update(appendHeaders)

        if self._debug:
            print(f"[Async] URL: {url}, TR: {tr_id}\n<header>\n{headers}\n<body>\n{params}")

        session = await self.get_session()
        try:
            if postFlag:
                async with session.post(url, headers=headers, json=params) as resp:
                    raw_data = await resp.json()
                    # We need to wrap aiohttp response into something APIResp compatible
                    # or modify APIResp to accept dict.
                    # APIResp currently takes requests.Response.
                    return self._create_api_resp_from_dict(resp.status, raw_data, dict(resp.headers))
            else:
                async with session.get(url, headers=headers, params=params) as resp:
                    raw_data = await resp.json()
                    return self._create_api_resp_from_dict(resp.status, raw_data, dict(resp.headers))
        except Exception as e:
            logger.error(f"Async fetch error: {e}")
            return APIRespError(500, str(e))

    def _create_api_resp_from_dict(self, status_code: int, body: dict, headers: dict) -> APIResp:
        # Mocking requests.Response for APIResp compatibility
        class MockResp:
            def __init__(self, s, b, h):
                self.status_code = s
                self._body = b
                self.headers = h
            def json(self): return self._body
        
        return APIResp(MockResp(status_code, body, headers))

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()


# Singleton Instance for backward compatibility during migration
# (Deprecation Warning: Prefer instantiating KISAuthManager directly)
try:
    _auth_manager = KISAuthManager()
    
    # Expose methods as global functions to maintain compatibility
    def auth(svr="prod", product=None, url=None):
        _auth_manager.auth(svr, product, url)
        
    def getEnv():
        return _auth_manager.get_config()
        
    def getTREnv():
        return _auth_manager.get_tr_env()
        
    def _url_fetch(api_url, ptr_id, tr_cont, params, appendHeaders=None, postFlag=False, hashFlag=True):
        return _auth_manager.url_fetch(api_url, ptr_id, tr_cont, params, appendHeaders, postFlag, hashFlag)
        
except Exception as e:
    logging.warning(f"Could not initialize global KISAuthManager: {e}")
    # This might happen if config is missing, etc. Consumers should handle errors.


# ---------------------------------------------------------------------------
# WebSocket support (Refactored to check global manager)
# ---------------------------------------------------------------------------

_base_headers_ws = {"content-type": "utf-8"}

def _getResultObject(json_data):
    """Convert JSON dict to namedtuple."""
    return namedtuple("res", json_data.keys())(**json_data)

def auth_ws(svr="prod", product=None):
    """Get WebSocket approval key."""
    # Note: access global manager config
    cfg = _auth_manager.get_config()
    product = product or cfg.get("my_prod", "01")
    
    p = {"grant_type": "client_credentials"}
    if svr == "prod":
        p["appkey"], p["secretkey"] = cfg["my_app"], cfg["my_sec"]
    else:
        p["appkey"], p["secretkey"] = cfg["paper_app"], cfg["paper_sec"]

    url = f"{cfg[svr]}/oauth2/Approval"
    
    # Use reAuth logic if needed, but for now strict request
    headers = copy.deepcopy(_auth_manager.base_headers)
    del headers["authorization"] # Approval key endpoint might not need bearer? actually it uses appkey/secret in body
    # Standard KIS docs say header content-type json.
    
    res = requests.post(url, data=json.dumps(p), headers={"content-type": "application/json"})
    if res.status_code == 200:
        _base_headers_ws["approval_key"] = _getResultObject(res.json()).approval_key
    else:
        print("Get Approval token fail!")
        return

    # Update global state if needed, or rely on manager
    # _auth_manager.changeTREnv... (already handled in auth)

def data_fetch(tr_id, tr_type, params, appendHeaders=None) -> dict:
    headers = copy.deepcopy(_base_headers_ws)
    headers["tr_type"] = tr_type
    headers["custtype"] = "P"
    if appendHeaders:
        headers.update(appendHeaders)
    inp = {"tr_id": tr_id}
    inp.update(params)
    return {"header": headers, "body": {"input": inp}}

def system_resp(data):
    """Parse WebSocket system response."""
    isPingPong = isUnSub = isOk = False
    tr_msg = tr_key = encrypt = iv = ekey = None

    rdic = json.loads(data)
    tr_id = rdic["header"]["tr_id"]
    if tr_id != "PINGPONG":
        tr_key = rdic["header"]["tr_key"]
        encrypt = rdic["header"]["encrypt"]

    if rdic.get("body") is not None:
        isOk = rdic["body"]["rt_cd"] == "0"
        tr_msg = rdic["body"]["msg1"]
        if "output" in rdic["body"]:
            iv = rdic["body"]["output"]["iv"]
            ekey = rdic["body"]["output"]["key"]
        isUnSub = tr_msg[:5] == "UNSUB"
    else:
        isPingPong = tr_id == "PINGPONG"

    nt = namedtuple("SysMsg", ["isOk", "tr_id", "tr_key", "isUnSub", "isPingPong", "tr_msg", "iv", "ekey", "encrypt"])
    return nt(isOk=isOk, tr_id=tr_id, tr_key=tr_key, isUnSub=isUnSub, isPingPong=isPingPong,
              tr_msg=tr_msg, iv=iv, ekey=ekey, encrypt=encrypt)

def aes_cbc_base64_dec(key, iv, cipher_text):
    if key is None or iv is None:
        raise AttributeError("key and iv cannot be None")
    cipher = AES.new(key.encode("utf-8"), AES.MODE_CBC, iv.encode("utf-8"))
    return bytes.decode(unpad(cipher.decrypt(b64decode(cipher_text)), AES.block_size))

# WebSocket maps
open_map: dict = {}
def add_open_map(name: str, request: Callable, data, kwargs: dict = None):
    if open_map.get(name) is None:
        open_map[name] = {"func": request, "items": [], "kwargs": kwargs}
    if type(data) is list:
        open_map[name]["items"] += data
    elif type(data) is str:
        open_map[name]["items"].append(data)

data_map: dict = {}
def add_data_map(tr_id: str, columns: list = None, encrypt: str = None, key: str = None, iv: str = None):
    if data_map.get(tr_id) is None:
        data_map[tr_id] = {"columns": [], "encrypt": False, "key": None, "iv": None}
    if columns is not None:
        data_map[tr_id]["columns"] = columns
    if encrypt is not None:
        data_map[tr_id]["encrypt"] = encrypt
    if key is not None:
        data_map[tr_id]["key"] = key
    if iv is not None:
        data_map[tr_id]["iv"] = iv

class KISWebSocket:
    def __init__(self, api_url: str, max_retries: int = 3):
        self.api_url = api_url
        self.max_retries = max_retries
        self.retry_count = 0
        self.on_result = None
        self.result_all_data = False

    async def __subscriber(self, ws):
        async for raw in ws:
            logging.info("received message >> %s" % raw)
            show_result = False
            df = pd.DataFrame()

            if raw[0] in ["0", "1"]:
                d1 = raw.split("|")
                if len(d1) < 4:
                    raise ValueError("data not found")
                tr_id = d1[1]
                dm = data_map[tr_id]
                d = d1[3]
                if dm.get("encrypt") == "Y":
                    d = aes_cbc_base64_dec(dm["key"], dm["iv"], d)
                df = pd.read_csv(StringIO(d), header=None, sep="^", names=dm["columns"], dtype=object)
                show_result = True
            else:
                rsp = system_resp(raw)
                tr_id = rsp.tr_id
                add_data_map(tr_id=rsp.tr_id, encrypt=rsp.encrypt, key=rsp.ekey, iv=rsp.iv)
                if rsp.isPingPong:
                    print(f"### RECV [PINGPONG] [{raw}]")
                    await ws.pong(raw)
                    print(f"### SEND [PINGPONG] [{raw}]")
                if self.result_all_data:
                    show_result = True

            if show_result and self.on_result is not None:
                self.on_result(ws, tr_id, df, data_map[tr_id])

    async def __runner(self):
        if len(open_map) > 40:
            raise ValueError("Max 40 subscriptions")
        # Use manager to get URL
        env = _auth_manager.get_tr_env()
        url = f"{env.my_url_ws}{self.api_url}"
        
        while self.retry_count < self.max_retries:
            try:
                async with websockets.connect(url) as ws:
                    for name, obj in open_map.items():
                        await self.send_multiple(ws, obj["func"], "1", obj["items"], obj["kwargs"])
                    await asyncio.gather(self.__subscriber(ws))
            except Exception as e:
                print(f"Connection exception >> {e}")
                self.retry_count += 1
                await asyncio.sleep(1)

    @classmethod
    async def send(cls, ws, request, tr_type, data, kwargs=None):
        k = kwargs or {}
        msg, columns = request(tr_type, data, **k)
        add_data_map(tr_id=msg["body"]["input"]["tr_id"], columns=columns)
        # logging.info("send message >> %s" % json.dumps(msg))
        await ws.send(json.dumps(msg))
        _auth_manager.smart_sleep()

    async def send_multiple(self, ws, request, tr_type, data, kwargs=None):
        if type(data) is str:
            await self.send(ws, request, tr_type, data, kwargs)
        elif type(data) is list:
            for d in data:
                await self.send(ws, request, tr_type, d, kwargs)
        else:
            raise ValueError("data must be str or list")

    @classmethod
    def subscribe(cls, request, data, kwargs=None):
        add_open_map(request.__name__, request, data, kwargs)

    def unsubscribe(self, ws, request, data):
        self.send_multiple(ws, request, "2", data)

    def start(self, on_result, result_all_data=False):
        self.on_result = on_result
        self.result_all_data = result_all_data
        try:
            asyncio.run(self.__runner())
        except KeyboardInterrupt:
            print("Closing by KeyboardInterrupt")
