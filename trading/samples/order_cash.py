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
# [Domestic Stock] Order/Account > Stock Order(Cash)[v1_Domestic Stock-001]
##############################################################################################

# Constants
API_URL = "/uapi/domestic-stock/v1/trading/order-cash"

def order_cash(
    env_dv: str,  # Real/Demo mode (real:live, demo:paper)
    ord_dv: str,  # Buy/Sell division (buy:buy, sell:sell)
    cano: str,  # Account number
    acnt_prdt_cd: str,  # Account product code
    pdno: str,  # Product number (stock code)
    ord_dvsn: str,  # Order division
    ord_qty: str,  # Order quantity
    ord_unpr: str,  # Order unit price
    excg_id_dvsn_cd: str,  # Exchange ID division code
    sll_type: str = "",  # Sell type (for sell orders)
    cndt_pric: str = ""  # Condition price
) -> pd.DataFrame:
    """
    Domestic stock order (cash) API.

    ※ TTC0802U (cash buy) can be used for margin buying. However, the trading account must be registered as a 40% margin account.
    ※ Credit buying has a separate API.

    ※ Please note that ORD_QTY (order quantity), ORD_UNPR (order unit price) must be passed as String.

    ※ Orders without ORD_UNPR (order unit price) will be ordered at the upper limit price and settled at the execution price after execution.

    ※ For POST API, BODY key values must be written in uppercase.
    (EX. "CANO" : "12345678", "ACNT_PRDT_CD": "01",...)

    ※ For stock code master file Python processing code, please refer to Korea Investment & Securities Github.
    https://github.com/koreainvestment/open-trading-api/tree/main/stocks_info

    Args:
        env_dv (str): [Required] Real/Demo mode (real:live, demo:paper)
        ord_dv (str): [Required] Buy/Sell division (buy:buy, sell:sell)
        cano (str): [Required] Account number (Account number)
        acnt_prdt_cd (str): [Required] Account product code (Product type code)
        pdno (str): [Required] Product number (Stock code (6 digits), 7 digits for ETN)
        ord_dvsn (str): [Required] Order division
        ord_qty (str): [Required] Order quantity
        ord_unpr (str): [Required] Order unit price
        excg_id_dvsn_cd (str): [Required] Exchange ID division code (KRX)
        sll_type (str): Sell type (for sell orders) (01:Regular sell,02:Discretionary,05:Securities lending sell)
        cndt_pric (str): Condition price (Used for stop limit orders)

    Returns:
        pd.DataFrame: Stock order result data

    Example:
        >>> df = order_cash(env_dv="demo", ord_dv="buy", cano=trenv.my_acct, acnt_prdt_cd=trenv.my_prod, pdno="005930", ord_dvsn="00", ord_qty="1", ord_unpr="70000", excg_id_dvsn_cd="KRX")
        >>> print(df)
    """

    # Validate required parameters
    if env_dv == "" or env_dv is None:
        raise ValueError("env_dv is required (e.g. 'real:live, demo:paper')")

    if ord_dv == "" or ord_dv is None:
        raise ValueError("ord_dv is required (e.g. 'buy:buy, sell:sell')")

    if cano == "" or cano is None:
        raise ValueError("cano is required (e.g. 'Account number')")

    if acnt_prdt_cd == "" or acnt_prdt_cd is None:
        raise ValueError("acnt_prdt_cd is required (e.g. 'Product type code')")

    if pdno == "" or pdno is None:
        raise ValueError("pdno is required (e.g. 'Stock code (6 digits), 7 digits for ETN')")

    if ord_dvsn == "" or ord_dvsn is None:
        raise ValueError("ord_dvsn is required (e.g. '')")

    if ord_qty == "" or ord_qty is None:
        raise ValueError("ord_qty is required (e.g. '')")

    if ord_unpr == "" or ord_unpr is None:
        raise ValueError("ord_unpr is required (e.g. '')")

    if excg_id_dvsn_cd == "" or excg_id_dvsn_cd is None:
        raise ValueError("excg_id_dvsn_cd is required (e.g. 'KRX')")

    # Set tr_id
    if env_dv == "real":
        if ord_dv == "sell":
            tr_id = "TTTC0011U"
        elif ord_dv == "buy":
            tr_id = "TTTC0012U"
        else:
            raise ValueError("ord_dv can only be sell or buy")
    elif env_dv == "demo":
        if ord_dv == "sell":
            tr_id = "VTTC0011U"
        elif ord_dv == "buy":
            tr_id = "VTTC0012U"
        else:
            raise ValueError("ord_dv can only be sell or buy")
    else:
        raise ValueError("env_dv is required (e.g. 'real' or 'demo')")

    params = {
        "CANO": cano,  # Account number
        "ACNT_PRDT_CD": acnt_prdt_cd,  # Account product code
        "PDNO": pdno,  # Product number
        "ORD_DVSN": ord_dvsn,  # Order division
        "ORD_QTY": ord_qty,  # Order quantity
        "ORD_UNPR": ord_unpr,  # Order unit price
        "EXCG_ID_DVSN_CD": excg_id_dvsn_cd,  # Exchange ID division code
        "SLL_TYPE": sll_type,  # Sell type
        "CNDT_PRIC": cndt_pric  # Condition price
    }
    
    res = ka._url_fetch(API_URL, tr_id, "", params, postFlag=True)
    
    if res.isOK():
        current_data = pd.DataFrame([res.getBody().output])
        return current_data
    else:
        res.printError(url=API_URL)
        return pd.DataFrame() 