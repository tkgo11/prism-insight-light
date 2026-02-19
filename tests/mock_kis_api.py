from typing import Dict, Any, Optional
import datetime

class MockKISApi:
    """
    Mock class to simulate KIS API responses for testing without actual network calls.
    Usage: Replace KISAuthManager.async_url_fetch with MockKISApi.async_url_fetch
    """
    def __init__(self):
        self.prices = {
            "005930": 70000, # Samsung Electronics
            "000660": 120000, # SK Hynix
            "AAPL": 150.0,
            "TSLA": 250.0
        }
    
    async def async_url_fetch(self, api_url: str, ptr_id: str, tr_cont: str, params: dict, 
                              appendHeaders: dict = None, postFlag: bool = False, 
                              market: str = "KR") -> 'APIResp':
        
        # Simulate Price Inquiry
        if "quotations/inquire-price" in api_url or "quotations/price" in api_url:
            ticker = params.get("FID_INPUT_ISCD") or params.get("SYMB")
            price = self.prices.get(ticker, 10000 if market == "KR" else 100.0)
            
            output = {
                "stck_prpr": str(price), # KRW
                "last": str(price)       # US
            }
            return self._make_resp(output)

        # Simulate Order
        if "order/cash" in api_url or "order/buy" in api_url or "order/sell" in api_url:
            # Check price/qty logic if needed, but for now just succeed
            output = {
                "KRX_FWDG_ORD_ORGNO": "91252",
                "ODNO": "0000112345", # KR Order No
                "ORD_NO": "0000112345" # US Order No
            }
            return self._make_resp(output)
            
        return self._make_resp({})

    def _make_resp(self, output: Dict) -> Any:
        # Mocking the structure of APIResp dataclass
        class MockResp:
            def __init__(self, data):
                self.rt_cd = "0"
                self.msg_cd = "MCA00000"
                self.msg1 = "Success"
                self.output = data
                self.header = {"tr_id": "MOCK"}
                
            def json(self):
                return {
                    "rt_cd": self.rt_cd,
                    "msg_cd": self.msg_cd,
                    "msg1": self.msg1,
                    "output": self.output
                }
            
            def isOK(self):
                return self.rt_cd == "0"
            
            def getBody(self):
                # Simple object to mimic APIResp.getBody() return which usually has 'output' attribute
                class Body:
                    def __init__(self, output):
                        self.output = output
                        self.rt_cd = "0"
                        self.msg_cd = "MCA00000"
                        self.msg1 = "Success"
                return Body(self.output)

        return MockResp(output)
