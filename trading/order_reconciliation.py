"""Read-only KIS order-history collection: never proof of a fill or permission to SELL."""
from datetime import datetime
import re


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _first(row, *keys):
    """Read a broker field without silently preferring contradictory aliases.

    KIS wrappers may expose alternate spellings.  A single row containing
    different order, fill, quantity, or cancel values under those spellings
    is ambiguous, not evidence that may be attributed to a BUY.
    """
    present = [row[key] for key in keys if key in row]
    if not present:
        return None
    first = present[0]
    if any(type(value) is not type(first) or value != first for value in present[1:]):
        raise ValueError("conflicting KIS order-history field aliases")
    return first


def _qty(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("boolean quantity")
    if isinstance(value, int) and value >= 0:
        return value
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,18}", value) is None:
        raise ValueError("noncanonical quantity")
    return int(value)


def _matched(row, *, order_no, order_date, ticker, account, product):
    if not isinstance(row, dict):
        raise ValueError("malformed history row")
    if (_first(row, "odno", "ODNO") != order_no
            or _first(row, "pdno", "PDNO", "ovrs_pdno") != ticker
            or _first(row, "ord_dt", "ORD_DT") != order_date
            or _first(row, "sll_buy_dvsn_cd", "sll_buy_dvsn", "SLL_BUY_DVSN_CD", "SLL_BUY_DVSN") != "02"):
        return None
    for key, value in (("cano", account), ("CANO", account),
                       ("acnt_prdt_cd", product), ("ACNT_PRDT_CD", product)):
        if key in row and row[key] != value:
            return None
    ordered = _qty(_first(row, "ord_qty", "ORD_QTY", "ft_ord_qty"))
    # Cumulative filled quantity and this execution's quantity are distinct KIS
    # facts. ccld_qty must not masquerade as a cumulative total or conflict
    # with a valid tot_ccld_qty / ft_ccld_qty on the same history row.
    filled = _qty(_first(row, "tot_ccld_qty", "ft_ccld_qty"))
    execution_qty = _qty(_first(row, "ccld_qty", "CCLD_QTY"))
    remaining = _qty(_first(row, "rmn_qty", "nccs_qty", "ord_remn_qty"))
    if execution_qty is not None and (
            (filled is not None and execution_qty > filled)
            or (ordered is not None and execution_qty > ordered)):
        raise ValueError("execution quantity exceeds cumulative or ordered quantity")
    if ordered is not None and ((filled or 0) + (remaining or 0) > ordered):
        raise ValueError("contradictory quantities")
    execution_id = _first(row, "ccld_no", "ccld_seq", "CCLD_NO")
    if execution_id is not None and (
            not isinstance(execution_id, str)
            or re.fullmatch(r"[A-Za-z0-9]{1,40}", execution_id) is None):
        raise ValueError("invalid execution ID")
    cancel_flag = _first(row, "cncl_yn", "CNCL_YN")
    if cancel_flag not in (None, "", "Y", "N"):
        raise ValueError("invalid cancel flag")
    return dict(order_no=order_no, order_date=order_date, ticker=ticker,
                order_quantity=ordered, filled_quantity=filled,
                execution_quantity=execution_qty, remaining_quantity=remaining,
                execution_id=execution_id,
                cancel_flag=cancel_flag or None)


def read_order_history(trader, *, market, order_no, order_date, ticker, max_pages=5):
    """Bounded read-only account-bound inquiry; returned rows are NON-ACTIONABLE.

    No stop-loss, order submission, ledger finalize, or balance mutation occurs.
    Missing rows never prove cancellation, rejection or absence of a late fill.
    """
    if market not in ("KR", "US"):
        raise ValueError("unknown market")
    if not isinstance(order_no, str) or re.fullmatch(r"[0-9]{1,20}", order_no) is None:
        raise ValueError("invalid order number")
    if not isinstance(order_date, str) or re.fullmatch(r"[0-9]{8}", order_date) is None:
        raise ValueError("invalid date")
    try:
        datetime.strptime(order_date, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("invalid date") from exc
    pattern = r"[0-9]{6}" if market == "KR" else r"[A-Z0-9.]{1,16}"
    if not isinstance(ticker, str) or re.fullmatch(pattern, ticker) is None:
        raise ValueError("invalid ticker")
    if type(max_pages) is not int or not 1 <= max_pages <= 20:
        raise ValueError("invalid page limit")
    env = getattr(trader, "trenv", None)
    account, product, scope = (getattr(env, "my_acct", None),
                               getattr(env, "my_prod", None),
                               getattr(trader, "account_key", None))
    if not all(isinstance(x, str) and x for x in (account, product, scope)):
        raise ValueError("missing account provenance")
    mode = getattr(trader, "mode", None)
    if mode not in ("demo", "real"):
        raise ValueError("unknown broker mode")
    result = dict(market=market, account_scope=scope, order_no=order_no,
                  order_date=order_date, ticker=ticker, status="UNVERIFIED",
                  complete=False, can_activate_stop=False, matches=[])
    if market == "KR":
        path = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        tr_id = "VTTC0081R" if mode == "demo" else "TTTC0081R"
        params = dict(CANO=account, ACNT_PRDT_CD=product, INQR_STRT_DT=order_date,
                      INQR_END_DT=order_date, SLL_BUY_DVSN_CD="02", PDNO=ticker,
                      CCLD_DVSN="00", INQR_DVSN="00", INQR_DVSN_3="00",
                      ORD_GNO_BRNO="", ODNO=order_no, INQR_DVSN_1="",
                      CTX_AREA_FK100="", CTX_AREA_NK100="")
        fk, nk, output = "CTX_AREA_FK100", "CTX_AREA_NK100", "output1"
    else:
        path = "/uapi/overseas-stock/v1/trading/inquire-ccnl"
        tr_id = "VTTS3035R" if mode == "demo" else "TTTS3035R"
        params = dict(CANO=account, ACNT_PRDT_CD=product,
                      PDNO="" if mode == "demo" else "%",
                      ORD_STRT_DT=order_date, ORD_END_DT=order_date,
                      SLL_BUY_DVSN="00" if mode == "demo" else "02",
                      CCLD_NCCS_DVSN="00", SORT_SQN="DS",
                      ORD_DT="", ORD_GNO_BRNO="", ODNO="",
                      OVRS_EXCG_CD="" if mode == "demo" else "%",
                      CTX_AREA_FK200="", CTX_AREA_NK200="")
        fk, nk, output = "CTX_AREA_FK200", "CTX_AREA_NK200", "output"
    cursors, records, matches, versions, order_quantities = set(), set(), [], {}, set()
    execution_quantities, total_execution_quantity = {}, 0
    cumulative_execution_ids, max_cumulative_quantity = set(), None
    for page in range(max_pages):
        try:
            response = trader._request(path, tr_id, dict(params), tr_cont="N" if page else "")
            if not response.isOK():
                return result
            body, header = response.getBody(), response.getHeader()
            # A wrapper can report transport success while the KIS response
            # body reports a business failure. Never accept such rows as
            # authoritative order, fill, cancel, or remaining-quantity evidence.
            # Treat malformed and contradictory status aliases as unverified.
            missing = object()
            business_status_seen = False
            for status_key in ("rt_cd", "RT_CD"):
                business_status = _get(body, status_key, missing)
                if business_status is missing:
                    continue
                business_status_seen = True
                if type(business_status) is not str or business_status != "0":
                    return result
            # A successful HTTP/SDK wrapper and plausible fill rows do not
            # establish KIS business success if the body omits rt_cd entirely.
            # Reject the entire inquiry, including previously collected pages.
            if not business_status_seen:
                return result
            rows = _get(body, output)
            if isinstance(rows, dict):
                rows = [rows]
            if not isinstance(rows, list):
                return result
            for row in rows:
                match = _matched(row, order_no=order_no, order_date=order_date,
                                 ticker=ticker, account=account, product=product)
                if match is not None:
                    # Separate execution IDs still belong to one order. A
                    # contradictory submitted quantity across its history
                    # cannot be trusted for partial-fill/cancel reconciliation.
                    # This is read-only evidence: never finalize or SELL here.
                    if match["order_quantity"] is not None:
                        order_quantities.add(match["order_quantity"])
                        if len(order_quantities) > 1:
                            return result
                    # Repeated pages may duplicate rows. Conflicting snapshots
                    # for the same execution identity cannot prove final fills.
                    key = (match["order_no"], match["execution_id"])
                    if key in versions and versions[key] != match:
                        return result
                    versions[key] = match
                    # Each distinct broker execution contributes at most once.
                    # Independently plausible fills cannot jointly exceed the
                    # submitted order quantity, even across separate pages.
                    # Missing execution IDs are not safe to aggregate; this
                    # check remains read-only and never authorizes a SELL.
                    execution_id = match["execution_id"]
                    execution_qty = match["execution_quantity"]
                    cumulative_qty = match["filled_quantity"]
                    if cumulative_qty is not None:
                        max_cumulative_quantity = (
                            cumulative_qty if max_cumulative_quantity is None
                            else max(max_cumulative_quantity, cumulative_qty)
                        )
                    if execution_id is not None and execution_qty is not None and cumulative_qty is not None:
                        cumulative_execution_ids.add(execution_id)
                    if (execution_id is not None and execution_qty is not None
                            and execution_id not in execution_quantities):
                        execution_quantities[execution_id] = execution_qty
                        total_execution_quantity += execution_qty
                    if (order_quantities and
                            total_execution_quantity > next(iter(order_quantities))):
                        return result
                    signature = tuple(match.values())
                    if signature not in records:
                        records.add(signature)
                        matches.append(match)
            continuation = _get(header, "tr_cont", None)
            if continuation in ("", "D", "E"):
                # Only at the final page can we compare distinct execution
                # quantities against the largest cumulative broker snapshot.
                # A later page may legitimately report a newer total. Require
                # cumulative evidence for every counted execution, otherwise
                # leave missing data non-actionable rather than guessing.
                if (execution_quantities
                        and len(cumulative_execution_ids) == len(execution_quantities)
                        and max_cumulative_quantity is not None
                        and total_execution_quantity > max_cumulative_quantity):
                    return result
                result.update(status="EVIDENCE_ONLY", complete=True, matches=matches)
                return result
            if continuation not in ("M", "F"):
                return result
            cursor = (_get(body, fk.lower(), ""), _get(body, nk.lower(), ""))
            if not all(isinstance(c, str) for c in cursor) or not any(cursor) or cursor in cursors:
                return result
            cursors.add(cursor)
            params[fk], params[nk] = cursor
        except (AttributeError, TypeError, ValueError, RuntimeError, OSError, KeyError):
            return result
    return result
