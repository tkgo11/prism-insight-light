"""Read-only KIS order-history collection: never proof of a fill or permission to SELL."""
from datetime import datetime
import re


def _get(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _cursor_component(body, lower_key, upper_key, default=""):
    """Reject contradictory pagination cursor spellings before using a page.

    KIS wrapper responses may expose lower/upper-case cursor fields. Two
    different values cannot identify a trustworthy next page, even when the
    lower-case value happens to lead to plausible fill/cancel records.
    """
    missing = object()
    lower = _get(body, lower_key, missing)
    upper = _get(body, upper_key, missing)
    if (lower is not missing and upper is not missing
            and (type(lower) is not type(upper) or lower != upper)):
        raise ValueError("conflicting KIS pagination cursor aliases")
    if lower is not missing:
        return lower
    if upper is not missing:
        return upper
    return default



def _history_output(body, output):
    """Reject contradictory lower/upper KIS history payload aliases.

    A successful response with two different execution/cancel/remaining lists
    cannot be trusted as complete order evidence. A single uppercase payload
    is accepted when the lowercase spelling is absent.
    """
    missing = object()
    lower = _get(body, output, missing)
    upper = _get(body, output.upper(), missing)
    if (lower is not missing and upper is not missing
            and (type(lower) is not type(upper) or lower != upper)):
        raise ValueError("conflicting KIS order-history output aliases")
    if lower is not missing:
        return lower
    if upper is not missing:
        return upper
    return None


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


def _us_venue(row):
    """Optional US exchange provenance for read-only history integrity.

    An order cannot have two explicit, contradictory exchange codes under
    the same account/trading-date/order identity. Missing codes do not prove
    a venue. This is only an evidence-consistency check, never fill authority.
    """
    venue = _first(row, "ovrs_excg_cd", "OVRS_EXCG_CD")
    if venue in (None, ""):
        return None
    if type(venue) is not str or re.fullmatch(r"[A-Z0-9]{2,8}", venue) is None:
        raise ValueError("invalid overseas exchange code")
    return venue


def _qty(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("boolean quantity")
    # JSON integer quantities must obey the same 18-digit canonical bound
    # as broker-provided numeric strings.  Reject oversized values before
    # they can become even read-only order/fill/cancel evidence.
    if isinstance(value, int) and 0 <= value < 10**18:
        return value
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,18}", value) is None:
        raise ValueError("noncanonical quantity")
    return int(value)


def _exceeds_known_order_bound(match, known_order_quantity):
    """Cross-row quantity evidence must respect the order's known bound.

    Some broker history snapshots omit ord_qty. A different row for the same
    account/date/order can still establish the submitted quantity. Never
    promote such an inconsistent snapshot to read-only evidence.
    """
    # An execution without an ID or cumulative snapshot cannot be counted
    # reliably with other executions, but it still cannot individually exceed
    # a submitted quantity proven by another row of the same order.
    return (
        (match["execution_quantity"] is not None
         and match["execution_quantity"] > known_order_quantity)
        or (match["filled_quantity"] or 0)
        + (match["remaining_quantity"] or 0) > known_order_quantity
    )


def _matched(row, *, order_no, order_date, ticker, account, product):
    if not isinstance(row, dict):
        raise ValueError("malformed history row")
    # Order numbers can be reused on different trading dates, so first
    # identify the target by both number and date. A different order is
    # skippable; a contradictory target-order identity is not.
    if _first(row, "odno", "ODNO") != order_no:
        return None
    # An order number may recur on another *valid* trading date, but a
    # matching number with a missing/malformed date cannot be established as
    # unrelated. Invalidate earlier paginated evidence rather than skipping it.
    row_date = _first(row, "ord_dt", "ORD_DT")
    if not isinstance(row_date, str) or re.fullmatch(r"[0-9]{8}", row_date) is None:
        raise ValueError("target order has missing or invalid trading date")
    try:
        datetime.strptime(row_date, "%Y%m%d")
    except ValueError as exc:
        raise ValueError("target order has invalid calendar date") from exc
    if row_date != order_date:
        return None
    for key, value in (("cano", account), ("CANO", account),
                       ("acnt_prdt_cd", product), ("ACNT_PRDT_CD", product)):
        if key in row and row[key] != value:
            # The row already identifies the requested BUY order.  A foreign
            # account or account-product code is contradictory broker evidence,
            # not an unrelated row that may be silently skipped.  Invalidate
            # the entire paginated inquiry, including earlier matches.
            raise ValueError("target order account/product provenance mismatch")
    # A single account/date/order identity cannot be both a BUY and SELL,
    # or refer to different instruments. Silently skipping a conflicting
    # later page would incorrectly preserve earlier EVIDENCE_ONLY matches.
    if (_first(row, "pdno", "PDNO", "ovrs_pdno", "OVRS_PDNO") != ticker
            or _first(row, "sll_buy_dvsn_cd", "sll_buy_dvsn", "SLL_BUY_DVSN_CD", "SLL_BUY_DVSN") != "02"):
        raise ValueError("target order ticker/side provenance mismatch")
    ordered = _qty(_first(row, "ord_qty", "ORD_QTY", "ft_ord_qty", "FT_ORD_QTY"))
    if ordered == 0:
        # Zero submitted shares cannot establish a BUY order, even if every
        # other fill/cancel/remaining field is numerically self-consistent.
        raise ValueError("zero-quantity order is not reconcilable evidence")
    # Cumulative filled quantity and this execution's quantity are distinct KIS
    # facts. ccld_qty must not masquerade as a cumulative total or conflict
    # with a valid tot_ccld_qty / ft_ccld_qty on the same history row.
    filled = _qty(_first(row, "tot_ccld_qty", "TOT_CCLD_QTY", "ft_ccld_qty", "FT_CCLD_QTY"))
    execution_qty = _qty(_first(row, "ccld_qty", "CCLD_QTY"))
    remaining = _qty(_first(row, "rmn_qty", "RMN_QTY", "nccs_qty", "NCCS_QTY", "ord_remn_qty", "ORD_REMN_QTY"))
    if execution_qty is not None and (
            (filled is not None and execution_qty > filled)
            or (ordered is not None and execution_qty > ordered)):
        raise ValueError("execution quantity exceeds cumulative or ordered quantity")
    if ordered is not None and ((filled or 0) + (remaining or 0) > ordered):
        raise ValueError("contradictory quantities")
    execution_id = _first(row, "ccld_no", "CCLD_NO", "ccld_seq", "CCLD_SEQ")
    if execution_id is not None and (
            not isinstance(execution_id, str)
            or re.fullmatch(r"[A-Za-z0-9]{1,40}", execution_id) is None):
        raise ValueError("invalid execution ID")
    if execution_qty is not None and execution_qty > 0 and execution_id is None:
        # A positive per-fill quantity without its broker execution ID
        # cannot be deduplicated across pages, retries, or restarts.
        # Reject the entire read-only snapshot; never infer fill authority.
        raise ValueError("positive execution quantity lacks execution ID")
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
    # An all-zero ODNO is a broker placeholder, not an attributable order.
    # Keep exact history lookup aligned with durable BUY receipt validation and
    # discovery; never query or promote a placeholder to read-only evidence.
    if (not isinstance(order_no, str)
            or re.fullmatch(r"[0-9]{1,20}", order_no) is None
            or not order_no.strip("0")):
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
    us_venues = set()
    total_rows = 0
    for page in range(max_pages):
        try:
            response = trader._request(path, tr_id, dict(params), tr_cont="N" if page else "")
            # SDK transport success must be an actual boolean True. A truthy
            # string/integer is not authenticated evidence of a successful
            # history inquiry, even if the body contains plausible fills.
            if response.isOK() is not True:
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
            rows = _history_output(body, output)
            if isinstance(rows, dict):
                rows = [rows]
            if not isinstance(rows, list) or len(rows) > 2000 - total_rows:
                # The complete inquiry must be bounded, not just each page.
                # Discard all earlier matches on an oversized later page.
                return result
            total_rows += len(rows)
            for row in rows:
                match = _matched(row, order_no=order_no, order_date=order_date,
                                 ticker=ticker, account=account, product=product)
                if match is not None:
                    if market == "US":
                        venue = _us_venue(row)
                        if venue is not None:
                            us_venues.add(venue)
                            if len(us_venues) > 1:
                                # One date/order cannot be two US venues.
                                # Discard earlier paginated evidence as well.
                                return result
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
            continuation = _cursor_component(header, "tr_cont", "TR_CONT", None)
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
                # A snapshot omitting ord_qty must still obey a submitted
                # quantity established by another row of this exact order.
                if order_quantities:
                    known_order_quantity = next(iter(order_quantities))
                    if any(_exceeds_known_order_bound(match, known_order_quantity)
                           for match in matches):
                        return result
                result.update(status="EVIDENCE_ONLY", complete=True, matches=matches)
                return result
            if continuation not in ("M", "F"):
                return result
            cursor = (_cursor_component(body, fk.lower(), fk),
                      _cursor_component(body, nk.lower(), nk))
            if not all(isinstance(c, str) for c in cursor) or not any(cursor) or cursor in cursors:
                return result
            cursors.add(cursor)
            params[fk], params[nk] = cursor
        except (AttributeError, TypeError, ValueError, RuntimeError, OSError, KeyError):
            return result
    return result


def discover_buy_order_candidates(trader, *, market, ticker, start_date,
                                  end_date, max_pages=5):
    """Bounded account-scoped UNKNOWN BUY history discovery, strictly read-only.

    Returns possible order IDs and execution/cancel/remaining evidence, NEVER
    broker-authoritative attribution of an unknown signal. Even one candidate
    is insufficient to clear an UNKNOWN ledger fence or activate a stop-loss.
    Dates are caller-supplied; no local/server clock is used to infer a KIS
    trading date. No broker order or ledger/tracker mutation is performed.
    """
    if market not in ("KR", "US"):
        raise ValueError("unknown market")
    ticker_pattern = r"[0-9]{6}" if market == "KR" else r"[A-Z0-9.]{1,16}"
    if not isinstance(ticker, str) or re.fullmatch(ticker_pattern, ticker) is None:
        raise ValueError("invalid ticker")
    if type(max_pages) is not int or not 1 <= max_pages <= 20:
        raise ValueError("invalid page limit")
    parsed_dates = []
    for value in (start_date, end_date):
        if not isinstance(value, str) or re.fullmatch(r"[0-9]{8}", value) is None:
            raise ValueError("invalid discovery date")
        try:
            parsed_dates.append(datetime.strptime(value, "%Y%m%d"))
        except ValueError as exc:
            raise ValueError("invalid discovery date") from exc
    if not 0 <= (parsed_dates[1] - parsed_dates[0]).days <= 6:
        raise ValueError("discovery window must be 1 to 7 inclusive days")
    env = getattr(trader, "trenv", None)
    account, product, scope = (
        getattr(env, "my_acct", None), getattr(env, "my_prod", None),
        getattr(trader, "account_key", None),
    )
    if not all(isinstance(x, str) and x for x in (account, product, scope)):
        raise ValueError("missing account provenance")
    mode = getattr(trader, "mode", None)
    if mode not in ("demo", "real"):
        raise ValueError("unknown broker mode")
    result = dict(
        market=market, account_scope=scope, ticker=ticker,
        start_date=start_date, end_date=end_date,
        status="UNVERIFIED", complete=False, can_activate_stop=False,
        candidates=[],
    )
    if market == "KR":
        path = "/uapi/domestic-stock/v1/trading/inquire-daily-ccld"
        tr_id = "VTTC0081R" if mode == "demo" else "TTTC0081R"
        params = dict(
            CANO=account, ACNT_PRDT_CD=product,
            INQR_STRT_DT=start_date, INQR_END_DT=end_date,
            SLL_BUY_DVSN_CD="02", PDNO=ticker, CCLD_DVSN="00",
            INQR_DVSN="00", INQR_DVSN_3="00", ORD_GNO_BRNO="",
            ODNO="", INQR_DVSN_1="", CTX_AREA_FK100="", CTX_AREA_NK100="",
        )
        fk, nk, output = "CTX_AREA_FK100", "CTX_AREA_NK100", "output1"
    else:
        path = "/uapi/overseas-stock/v1/trading/inquire-ccnl"
        tr_id = "VTTS3035R" if mode == "demo" else "TTTS3035R"
        params = dict(
            CANO=account, ACNT_PRDT_CD=product,
            PDNO="" if mode == "demo" else "%",
            ORD_STRT_DT=start_date, ORD_END_DT=end_date,
            SLL_BUY_DVSN="00" if mode == "demo" else "02",
            CCLD_NCCS_DVSN="00", SORT_SQN="DS", ORD_DT="",
            ORD_GNO_BRNO="", ODNO="",
            OVRS_EXCG_CD="" if mode == "demo" else "%",
            CTX_AREA_FK200="", CTX_AREA_NK200="",
        )
        fk, nk, output = "CTX_AREA_FK200", "CTX_AREA_NK200", "output"
    cursors, all_rows = set(), []
    for page in range(max_pages):
        try:
            response = trader._request(
                path, tr_id, dict(params), tr_cont="N" if page else "",
            )
            # SDK transport success must be an actual boolean True. A truthy
            # string/integer is not authenticated evidence of a successful
            # history inquiry, even if the body contains plausible fills.
            if response.isOK() is not True:
                return result
            body, header = response.getBody(), response.getHeader()
            missing = object()
            statuses = [_get(body, key, missing) for key in ("rt_cd", "RT_CD")]
            statuses = [value for value in statuses if value is not missing]
            if not statuses or any(type(value) is not str or value != "0"
                                   for value in statuses):
                return result
            rows = _history_output(body, output)
            if isinstance(rows, dict):
                rows = [rows]
            if not isinstance(rows, list) or len(all_rows) + len(rows) > 2000:
                return result
            if not all(isinstance(row, dict) for row in rows):
                return result
            all_rows.extend(rows)
            continuation = _cursor_component(header, "tr_cont", "TR_CONT", None)
            if continuation in ("", "D", "E"):
                break
            if continuation not in ("M", "F"):
                return result
            cursor = (_cursor_component(body, fk.lower(), fk),
                      _cursor_component(body, nk.lower(), nk))
            if (not all(isinstance(c, str) for c in cursor)
                    or not any(cursor) or cursor in cursors):
                return result
            cursors.add(cursor)
            params[fk], params[nk] = cursor
        except (AttributeError, TypeError, ValueError, RuntimeError, OSError, KeyError):
            return result
    else:
        return result

    try:
        candidate_keys, matched, candidate_venues = set(), [], {}
        side_fields = (
            "sll_buy_dvsn_cd", "sll_buy_dvsn",
            "SLL_BUY_DVSN_CD", "SLL_BUY_DVSN",
        )
        for row in all_rows:
            row_ticker = _first(row, "pdno", "PDNO", "ovrs_pdno", "OVRS_PDNO")
            row_side = _first(row, *side_fields)
            if row_ticker != ticker or row_side != "02":
                continue
            order_no = _first(row, "odno", "ODNO")
            order_date = _first(row, "ord_dt", "ORD_DT")
            if (not isinstance(order_no, str)
                    or re.fullmatch(r"[0-9]{1,20}", order_no) is None
                    or not order_no.strip("0")):
                return result
            if (not isinstance(order_date, str)
                    or re.fullmatch(r"[0-9]{8}", order_date) is None):
                return result
            parsed_date = datetime.strptime(order_date, "%Y%m%d")
            if not parsed_dates[0] <= parsed_date <= parsed_dates[1]:
                return result
            match = _matched(
                row, order_no=order_no, order_date=order_date,
                ticker=ticker, account=account, product=product,
            )
            if match is None:
                return result
            if market == "US":
                # ODNO can be reused on different dates. Only compare
                # explicit venue codes within the same date-scoped order.
                venue = _us_venue(row)
                if venue is not None:
                    venues = candidate_venues.setdefault(
                        (order_no, order_date), set(),
                    )
                    venues.add(venue)
                    if len(venues) > 1:
                        return result
            candidate_keys.add((order_no, order_date))
            matched.append(match)
        # A single account/date/order number cannot identify two instruments
        # or both BUY and SELL. Inspect the entire page set, not just rows
        # encountered after a candidate was first seen.
        candidate_order_numbers = {number for number, _ in candidate_keys}
        for row in all_rows:
            row_number = _first(row, "odno", "ODNO")
            row_date = _first(row, "ord_dt", "ORD_DT")
            # The same ODNO may recur on a DIFFERENT VALID trading date.
            # Missing/malformed dates cannot establish that a contradictory
            # ticker/side row belongs to another order. Poison all candidates.
            if row_number in candidate_order_numbers:
                if (not isinstance(row_date, str)
                        or re.fullmatch(r"[0-9]{8}", row_date) is None):
                    return result
                try:
                    datetime.strptime(row_date, "%Y%m%d")
                except ValueError:
                    return result
            identity = (row_number, row_date)
            if identity in candidate_keys and (
                _first(row, "pdno", "PDNO", "ovrs_pdno", "OVRS_PDNO") != ticker
                or _first(row, *side_fields) != "02"
            ):
                return result
        versions, signatures, candidates, per_order = {}, set(), [], {}
        for match in matched:
            identity = (match["order_no"], match["order_date"])
            state = per_order.setdefault(identity, dict(
                ordered=set(), executions={}, cumulative_ids=set(), max_cum=None,
                snapshots=[],
            ))
            state["snapshots"].append(match)
            ordered = match["order_quantity"]
            if ordered is not None:
                state["ordered"].add(ordered)
                if len(state["ordered"]) > 1:
                    return result
            execution_id = match["execution_id"]
            key = (*identity, execution_id)
            if key in versions and versions[key] != match:
                return result
            versions[key] = match
            qty, cumulative = (
                match["execution_quantity"], match["filled_quantity"],
            )
            if cumulative is not None:
                state["max_cum"] = (
                    cumulative if state["max_cum"] is None
                    else max(state["max_cum"], cumulative)
                )
            if execution_id is not None and qty is not None:
                state["executions"][execution_id] = qty
                if cumulative is not None:
                    state["cumulative_ids"].add(execution_id)
            signature = tuple(match.values())
            if signature not in signatures:
                signatures.add(signature)
                candidates.append(match)
        for state in per_order.values():
            # A known submitted quantity applies to every snapshot of this
            # account/date/order, even when an individual row omits ord_qty.
            if state["ordered"]:
                known_order_quantity = next(iter(state["ordered"]))
                if any(_exceeds_known_order_bound(match, known_order_quantity)
                       for match in state["snapshots"]):
                    return result
            total = sum(state["executions"].values())
            if state["ordered"] and total > next(iter(state["ordered"])):
                return result
            if (state["executions"]
                    and len(state["cumulative_ids"]) == len(state["executions"])
                    and state["max_cum"] is not None
                    and total > state["max_cum"]):
                return result
        result.update(status="CANDIDATES_ONLY", complete=True, candidates=candidates)
        return result
    except (AttributeError, TypeError, ValueError, RuntimeError, OSError, KeyError):
        return result
