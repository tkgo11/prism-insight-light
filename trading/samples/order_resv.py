"""
Created on 20250112 
@author: LaivData SJPark with cursor
"""


import sys
from typing import Optional
import logging

import pandas as pd

sys.path.extend(['../..', '.'])
import kis_auth as ka

# Logging configuration
logging.basicConfig(level=logging.INFO)

##############################################################################################
# [Domestic Stock] Order/Account > Stock Reserved Order[v1_Domestic Stock-017]
##############################################################################################

# Constants
API_URL = "/uapi/domestic-stock/v1/trading/order-resv"

def order_resv(
    cano: str,
    acnt_prdt_cd: str,
    pdno: str,
    ord_qty: str,
    ord_unpr: str,
    sll_buy_dvsn_cd: str,
    ord_dvsn_cd: str,
    ord_objt_cblc_dvsn_cd: str,
    loan_dt: Optional[str] = "",
    rsvn_ord_end_dt: Optional[str] = "",
    ldng_dt: Optional[str] = ""
) -> pd.DataFrame:
    """
    Domestic stock reserved order buy/sell API.

    ※ For POST API, BODY key values must be written in uppercase.
    (EX. "CANO" : "12345678", "ACNT_PRDT_CD": "01",...)

    ※ Important Notes
    1. Reserved order available time: 15:40 ~ next business day 7:30
        (Except server initialization: 23:40 ~ 00:10)
        ※ Reserved order processing results are not notified, so please check order processing results before market open.

    2. Reserved order guide
    - If end date not entered: Regular reserved order, order sent on first arriving business day.
    - If end date entered: Period reserved order, unfilled quantity from initial order executed every business day until end date.
        (End date can be entered up to 30 calendar days including holidays from next business day)
    - Order processing priority: Earlier application date takes precedence between regular/period reserved orders.
        However, orders received during period reserved order auto-batch time (approx. 15:35 ~ 15:55) may be processed
        regardless of order on that day only.
    - Period reserved order inquiry may be restricted during auto-batch time (approx. 15:35 ~ 15:55).
    - Period reserved orders are limited to maximum 1,000 orders per account.

    3. Reserved orders may be rejected for reasons below, so please check order processing results before market open.
        * As of order processing date: Insufficient buy available amount, insufficient sell available quantity, order quantity/tick unit error,
        securities lending quote limit, credit/lending eligible stock change, upper/lower limit change, market price for price formation stocks
        (newly listed), trading service not subscribed, etc.

    4. Next day expected upper/lower limit is calculated from current price at inquiry time and may change due to next day's
        paid-in/bonus capital increase, dividend, capital reduction, merger, par value change, etc. This may cause order rejection
        due to exceeding upper/lower limit, so please check order processing results before market open.

    5. Cleanup trading stocks, ELW, stock warrants, stock warrant certificates are exempt from price limit (upper/lower limit).

    6. Canceling [Period Reserved Order] after business day market open only cancels reserved orders after that point,
        and does not affect orders already converted to regular orders. Please check order processing results before market open.

    Args:
        cano (str): [Required] Account number (First 8 digits of account number format 8-2)
        acnt_prdt_cd (str): [Required] Account product code (Last 2 digits of account number format 8-2)
        pdno (str): [Required] Stock code (6 digits)
        ord_qty (str): [Required] Order quantity (Number of shares)
        ord_unpr (str): [Required] Order unit price (Price per share, 0 for market price/pre-market after-hours)
        sll_buy_dvsn_cd (str): [Required] Sell/Buy division code (01: Sell, 02: Buy)
        ord_dvsn_cd (str): [Required] Order division code (00: Limit, 01: Market, 02: Conditional limit, 05: Pre-market after-hours)
        ord_objt_cblc_dvsn_cd (str): [Required] Order target balance division code (10: Cash, 12~28: Various loan/repayment codes)
        loan_dt (Optional[str]): Loan date
        rsvn_ord_end_dt (Optional[str]): Reserved order end date (YYYYMMDD, up to 30 days from next business day)
        ldng_dt (Optional[str]): Lending date

    Returns:
        pd.DataFrame: Reserved order result data

    Example:
        >>> df = order_resv(cano=trenv.my_acct, acnt_prdt_cd=trenv.my_prod, pdno="005930", ord_qty="1", ord_unpr="55000", sll_buy_dvsn_cd="02", ord_dvsn_cd="00", ord_objt_cblc_dvsn_cd="10")
        >>> print(df)
    """

    if cano == "" or cano is None:
        raise ValueError("cano is required (e.g. 'First 8 digits of account number format 8-2')")

    if acnt_prdt_cd == "" or acnt_prdt_cd is None:
        raise ValueError("acnt_prdt_cd is required (e.g. 'Last 2 digits of account number format 8-2')")

    if pdno == "" or pdno is None:
        raise ValueError("pdno is required (e.g. 'Stock code (6 digits)')")

    if ord_qty == "" or ord_qty is None:
        raise ValueError("ord_qty is required (e.g. 'Number of shares')")

    if ord_unpr == "" or ord_unpr is None:
        raise ValueError("ord_unpr is required (e.g. 'Price per share, 0 for market price/pre-market after-hours')")

    if sll_buy_dvsn_cd == "" or sll_buy_dvsn_cd is None:
        raise ValueError("sll_buy_dvsn_cd is required (e.g. '01: Sell, 02: Buy')")

    if ord_dvsn_cd == "" or ord_dvsn_cd is None:
        raise ValueError("ord_dvsn_cd is required (e.g. '00: Limit, 01: Market, 02: Conditional limit, 05: Pre-market after-hours')")

    if ord_objt_cblc_dvsn_cd == "" or ord_objt_cblc_dvsn_cd is None:
        raise ValueError("ord_objt_cblc_dvsn_cd is required (e.g. '10: Cash, 12~28: Various loan/repayment codes')")

    tr_id = "CTSC0008U"

    params = {
        "CANO": cano,
        "ACNT_PRDT_CD": acnt_prdt_cd,
        "PDNO": pdno,
        "ORD_QTY": ord_qty,
        "ORD_UNPR": ord_unpr,
        "SLL_BUY_DVSN_CD": sll_buy_dvsn_cd,
        "ORD_DVSN_CD": ord_dvsn_cd,
        "ORD_OBJT_CBLC_DVSN_CD": ord_objt_cblc_dvsn_cd
    }
    
    if loan_dt:
        params["LOAN_DT"] = loan_dt
    if rsvn_ord_end_dt:
        params["RSVN_ORD_END_DT"] = rsvn_ord_end_dt
    if ldng_dt:
        params["LDNG_DT"] = ldng_dt
    
    res = ka._url_fetch(API_URL, tr_id, "", params, postFlag=True)
    
    if res.isOK():
        current_data = pd.DataFrame(res.getBody().output, index=[0])
        return current_data
    else:
        res.printError(url=API_URL)
        return pd.DataFrame() 