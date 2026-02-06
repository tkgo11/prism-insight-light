"""
Created on 20250112
@author: LaivData SJPark with cursor
"""


import sys
import logging

import pandas as pd

sys.path.extend(['../..', '.'])
import kis_auth as ka

# Logging configuration
logging.basicConfig(level=logging.INFO)

##############################################################################################
# [Domestic Stock] Basic Quote > Stock Current Price Quote[v1_Domestic Stock-008]
##############################################################################################

# Constants
API_URL = "/uapi/domestic-stock/v1/quotations/inquire-price"

def inquire_price(
    env_dv: str,  # [Required] Real/Demo mode (ex. real:live, demo:paper)
    fid_cond_mrkt_div_code: str,  # [Required] Condition market division code (ex. J:KRX, NX:NXT, UN:Unified)
    fid_input_iscd: str  # [Required] Input stock code (ex. Stock code (ex 005930 Samsung), ETN must add Q before 6-digit code)
) -> pd.DataFrame:
    """
    Stock current price quote API. Use websocket API for real-time quotes.

    ※ For stock code master file Python processing code, please refer to Korea Investment & Securities Github.
    https://github.com/koreainvestment/open-trading-api/tree/main/stocks_info

    Args:
        env_dv (str): [Required] Real/Demo mode (ex. real:live, demo:paper)
        fid_cond_mrkt_div_code (str): [Required] Condition market division code (ex. J:KRX, NX:NXT, UN:Unified)
        fid_input_iscd (str): [Required] Input stock code (ex. Stock code (ex 005930 Samsung), ETN must add Q before 6-digit code)

    Returns:
        pd.DataFrame: Stock current price quote data

    Example:
        >>> df = inquire_price("real", "J", "005930")
        >>> print(df)
    """

    # Validate required parameters
    if env_dv == "" or env_dv is None:
        raise ValueError("env_dv is required (e.g. 'real:live, demo:paper')")

    if fid_cond_mrkt_div_code == "" or fid_cond_mrkt_div_code is None:
        raise ValueError("fid_cond_mrkt_div_code is required (e.g. 'J:KRX, NX:NXT, UN:Unified')")

    if fid_input_iscd == "" or fid_input_iscd is None:
        raise ValueError("fid_input_iscd is required (e.g. 'Stock code (ex 005930 Samsung), ETN must add Q before 6-digit code')")

    # Set tr_id
    if env_dv == "real":
        tr_id = "FHKST01010100"
    elif env_dv == "demo":
        tr_id = "FHKST01010100"
    else:
        raise ValueError("env_dv can only be 'real' or 'demo'")

    params = {
        "FID_COND_MRKT_DIV_CODE": fid_cond_mrkt_div_code,
        "FID_INPUT_ISCD": fid_input_iscd
    }
    
    res = ka._url_fetch(API_URL, tr_id, "", params)
    
    if res.isOK():
        current_data = pd.DataFrame(res.getBody().output, index=[0])
        return current_data
    else:
        res.printError(url=API_URL)
        return pd.DataFrame() 