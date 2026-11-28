"""Scoped FULL_POSITION_MANAGED broker boundary.

No constructor authenticates or creates a broker. Callers supply an already
account-pinned trader. Notifications invalidate observations; they never fill
an order. KIS offers no transaction spanning external MTS orders and these
queries, so a fresh observation cannot promise atomic external serialization.

Official contracts (read, never executed during development):
https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/domestic_stock/inquire_psbl_rvsecncl
https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/inquire_nccs
https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/domestic_stock/order_resv_ccnl
https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/order_resv_list
https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/domestic_stock/ccnl_notice
https://github.com/koreainvestment/open-trading-api/tree/main/examples_llm/overseas_stock/ccnl_notice

Native cash queries below retain sellable quantity and fail on incomplete
pagination. A trusted reservation reader is additionally necessary: the
date-window reservation APIs cannot establish arbitrary active external
reservation coverage by guessing a date window. Demo open-order support is
not inferred from a real-only TR ID. Unsupported evidence always pauses.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
import re
from threading import RLock

from .execution_outcome import broker_order_date, broker_receipt_metadata, classify_broker_result
from .order_reconciliation import _account_provenance, _cursor_component, _first, _get, _history_output
from .portfolio_pagination import validated_balance_quantity


class EvidenceUnavailable(RuntimeError):
    """Evidence cannot authorize another financial action."""


class SubmissionNotStarted(EvidenceUnavailable):
    """Trusted local preflight ended before the cash submission method ran."""

    def __init__(self, entry, token, reason):
        super().__init__(reason)
        self.account_scope = _value(entry, "account_scope")
        self.market = _value(entry, "market")
        self.ticker = _value(entry, "ticker")
        self.token = token
        self.reason = reason


def _value(obj, key, default=None):
    return obj.get(key, default) if isinstance(obj, dict) else getattr(obj, key, default)


def _count(value, *, positive=False):
    if type(value) is not int or not (0 < value < 10**18 if positive else 0 <= value < 10**18):
        raise EvidenceUnavailable("invalid whole-share quantity")
    return value


def _date(value):
    if not isinstance(value, str) or broker_order_date({"order_date": value}) != value:
        raise EvidenceUnavailable("missing or invalid broker order date")
    return value


def _order_no(value):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,20}", value) is None or not value.strip("0"):
        raise EvidenceUnavailable("missing or invalid broker order number")
    return value


def _time(value):
    if not isinstance(value, str):
        raise EvidenceUnavailable("missing observation timestamp")
    try:
        result = datetime.fromisoformat(value)
    except ValueError as exc:
        raise EvidenceUnavailable("invalid observation timestamp") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise EvidenceUnavailable("observation timestamp must be timezone aware")
    return result.astimezone(timezone.utc)


class ManagedBrokerGateway:
    """Validate complete account evidence and submit only exact cash SELLs.

    An injected snapshot reader must return all active orders, including
    reservations and manual/external orders for the instrument. Its complete
    flag is an explicit provider contract, never synthesized from holdings.
    The native reader requires a separate authoritative reservation reader;
    without one it raises instead of claiming full coverage.
    """

    def __init__(self, *, trader, market, snapshot_reader=None, order_reader=None,
                 reservation_reader=None, now=None, max_age_seconds=5, max_pages=20):
        if market not in ("KR", "US"):
            raise ValueError("unsupported market")
        if type(max_pages) is not int or not 1 <= max_pages <= 20:
            raise ValueError("invalid page bound")
        if type(max_age_seconds) not in (int, float) or not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("invalid freshness bound")
        self.trader, self.market = trader, market
        self.snapshot_reader = snapshot_reader
        self.order_reader = order_reader
        self.reservation_reader = reservation_reader
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.max_age_seconds, self.max_pages = max_age_seconds, max_pages
        self._invalidated = {}
        self._last_clock = None
        self._lock = RLock()

    def _identity(self, entry):
        market, ticker, scope = (_value(entry, key) for key in ("market", "ticker", "account_scope"))
        pattern = r"[0-9]{6}" if market == "KR" else r"[A-Z0-9.]{1,16}"
        if (market != self.market or not isinstance(ticker, str) or re.fullmatch(pattern, ticker) is None
                or not isinstance(scope, str) or re.fullmatch(r"[0-9a-f]{64}", scope) is None):
            raise EvidenceUnavailable("invalid managed instrument identity")
        try:
            context = _account_provenance(self.trader)
        except (ValueError, TypeError, AttributeError) as exc:
            raise EvidenceUnavailable("authenticated account provenance unavailable") from exc
        if context[2] != scope:
            raise EvidenceUnavailable("authenticated account scope changed")
        return market, ticker, scope

    def _clock(self):
        clock = self.now()
        if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
            raise EvidenceUnavailable("freshness clock unavailable")
        clock = clock.astimezone(timezone.utc)
        if self._last_clock is not None and clock < self._last_clock:
            raise EvidenceUnavailable("freshness clock moved backwards")
        self._last_clock = clock
        return clock

    def _fresh(self, timestamp):
        when = _time(timestamp)
        age = (self._clock() - when).total_seconds()
        if not 0 <= age <= self.max_age_seconds:
            raise EvidenceUnavailable("stale or future broker observation")
        return when

    def snapshot(self, entry):
        with self._lock:
            identity = self._identity(entry)
            reader = self.snapshot_reader or self._native_snapshot
            try:
                result = deepcopy(reader(entry))
            except EvidenceUnavailable:
                raise
            except Exception as exc:
                raise EvidenceUnavailable("broker snapshot inquiry failed") from exc
            if self._identity(entry) != identity:
                raise EvidenceUnavailable("account changed during snapshot")
            if not isinstance(result, dict) or result.get("complete") is not True:
                raise EvidenceUnavailable("unsupported or incomplete account snapshot")
            for key, expected in zip(("market", "ticker", "account_scope"), identity):
                if result.get(key) != expected:
                    raise EvidenceUnavailable("snapshot instrument/account mismatch")
            when = self._fresh(result.get("as_of"))
            if identity in self._invalidated and when <= self._invalidated[identity]:
                raise EvidenceUnavailable("notification requires a new account inquiry")
            holding = _count(result.get("holding_quantity"))
            sellable = _count(result.get("sellable_quantity"))
            if sellable > holding:
                raise EvidenceUnavailable("sellable quantity exceeds confirmed holdings")
            price = result.get("current_price")
            if type(price) not in (int, float) or not math.isfinite(price) or price <= 0:
                raise EvidenceUnavailable("fresh quote unavailable")
            orders = result.get("open_orders")
            if not isinstance(orders, list) or len(orders) > 2000 or not all(isinstance(order, dict) for order in orders):
                raise EvidenceUnavailable("complete open-order evidence unavailable")
            if self.market == "US" and result.get("order_venue") not in ("NASD", "NYSE", "AMEX"):
                raise EvidenceUnavailable("US execution venue unavailable")
            return result

    def notify_hint(self, entry, notification=None):
        """Validate routing then invalidate; all notification fill fields ignored.

        A stream disconnect/reconnect should invoke this for each enrolled
        instrument too. Websocket transport is intentionally not started here.
        """
        with self._lock:
            identity = self._identity(entry)
            if notification is not None and not isinstance(notification, dict):
                raise EvidenceUnavailable("malformed notification")
            for key, expected in zip(("market", "ticker", "account_scope"), identity):
                if notification is not None and key in notification and notification[key] != expected:
                    raise EvidenceUnavailable("notification instrument/account mismatch")
            self._invalidated[identity] = self._clock()

    def submit_exact_sell(self, entry, quantity, *, reservation_id):
        with self._lock:
            if not isinstance(reservation_id, str) or not reservation_id:
                raise EvidenceUnavailable("durable reservation token missing")
            try:
                # Neither an injected complete snapshot nor a reservation
                # reader proves atomic exclusion of orders entered from MTS,
                # HTS or another PRISM process. Until a broker-documented
                # cross-channel atomic operation is implemented and verified,
                # real-account cash SELL must stop before any broker method.
                if getattr(self.trader, "mode", None) != "demo":
                    raise EvidenceUnavailable("broker atomic cross-channel order exclusion unverified")
                if not callable(self.order_reader):
                    raise EvidenceUnavailable("authoritative SELL recovery provider unavailable")
                _count(quantity, positive=True)
                current = self.snapshot(entry)
                if (current["open_orders"] or quantity != current["holding_quantity"]
                        or quantity != current["sellable_quantity"]):
                    raise EvidenceUnavailable("orders or insufficient sellable holdings block SELL")
                decision_time = _value(entry, "decision_as_of")
                if decision_time is not None and self._fresh(current["as_of"]) < self._fresh(decision_time):
                    raise EvidenceUnavailable("pre-submit snapshot predates the exit decision")
                decision_venue = _value(entry, "decision_venue")
                if decision_venue is not None and decision_venue != current.get("order_venue"):
                    raise EvidenceUnavailable("pre-submit venue differs from the exit decision")
                self._identity(entry)
                can_submit = _value(entry, "decision_can_submit")
                if can_submit is not None and can_submit(self.market) is not True:
                    raise EvidenceUnavailable("market closed during exit preflight")
                self._fresh(current["as_of"])
                # The authorization callback may run configuration readers or
                # other code. Recheck provenance and the demo-only gate after
                # it returns, before invoking the cash adapter.
                if getattr(self.trader, "mode", None) != "demo":
                    raise EvidenceUnavailable("broker mode changed during exit preflight")
                self._identity(entry)
            except EvidenceUnavailable as exc:
                # This is the only location producing a known-no-submission
                # result. Never wrap a broker/transport exception in this type.
                raise SubmissionNotStarted(entry, reservation_id, str(exc)) from exc
            kwargs = {"holding_quantity": quantity}
            if self.market == "US":
                kwargs.update(exchange=current["order_venue"], limit_price=current["current_price"])
            try:
                # Direct cash API only. Never smart_sell_all, fractions, or an
                # adapter balance-derived all-position quantity fallback.
                result = self.trader.sell_all_market_price(_value(entry, "ticker"), **kwargs)
            except Exception:
                return {"outcome": "UNKNOWN", "receipt": None}
            try:
                self._identity(entry)
            except EvidenceUnavailable:
                return {"outcome": "UNKNOWN", "receipt": None}
            status = classify_broker_result(result)
            receipt = None
            if isinstance(result, dict):
                metadata = broker_receipt_metadata(result)
                try:
                    number = _order_no(result.get("order_no"))
                except EvidenceUnavailable:
                    number = None
                if number is not None:
                    receipt = {"order_no": number, **metadata}
                    date = broker_order_date(result)
                    if date is not None: receipt["order_date"] = date
                if (metadata.get("order_kind") != "broker_order"
                        or metadata.get("order_quantity") != quantity
                        or (self.market == "US" and metadata.get("order_venue") != current["order_venue"])):
                    status = "unknown"
                    receipt = None
                if status == "failed" and result.get("outcome_unknown") is False:
                    code = result.get("business_error_code")
                    # Free-form diagnostics cannot be turned into proof.
                    if (result.get("verified_no_order") is True and receipt is None
                            and isinstance(code, str) and re.fullmatch(r"[A-Z]{2,8}[0-9]{3,8}", code)):
                        return {"outcome": "REJECTED", "receipt": None,
                                "rejection_proof": dict(
                                    account_scope=_value(entry, "account_scope"), market=self.market,
                                    ticker=_value(entry, "ticker"), quantity=quantity,
                                    token=reservation_id, code=code,
                                    source="BROKER_VERIFIED_REJECTION", verified_no_order=True)}
            return {"outcome": "ACCEPTED" if status == "executed" else "UNKNOWN", "receipt": receipt}

    def reconcile_order(self, entry, attempt):
        with self._lock:
            identity = self._identity(entry)
            if not isinstance(attempt, dict):
                raise EvidenceUnavailable("invalid durable attempt")
            receipt = attempt.get("receipt")
            quantity = _count(attempt.get("quantity"), positive=True)
            if (not isinstance(receipt, dict) or receipt.get("order_kind") != "broker_order"
                    or type(receipt.get("order_quantity")) is not int or receipt["order_quantity"] != quantity):
                raise EvidenceUnavailable("dated regular order receipt required")
            number, date = _order_no(receipt.get("order_no")), _date(receipt.get("order_date"))
            reader = self.order_reader or self._native_order
            try:
                result = deepcopy(reader(entry, attempt))
            except EvidenceUnavailable:
                raise
            except Exception as exc:
                raise EvidenceUnavailable("order status inquiry failed") from exc
            self._identity(entry)
            if not isinstance(result, dict) or result.get("complete") is not True or type(result.get("terminal")) is not bool:
                raise EvidenceUnavailable("incomplete order status")
            observed = self._fresh(result.get("as_of"))
            if attempt.get("created_at") is not None and observed < _time(attempt["created_at"]):
                raise EvidenceUnavailable("order status predates the reserved attempt")
            expected = dict(zip(("market", "ticker", "account_scope"), identity))
            expected.update(order_no=number, order_date=date, order_quantity=quantity)
            if any(type(result.get(key)) is not type(value) or result[key] != value for key, value in expected.items()):
                raise EvidenceUnavailable("order identity mismatch")
            if self.market == "US" and (receipt.get("order_venue") not in ("NASD", "NYSE", "AMEX")
                                         or result.get("order_venue") != receipt["order_venue"]):
                raise EvidenceUnavailable("order venue mismatch")
            filled, cancelled, remaining = (_count(result.get(key)) for key in (
                "filled_quantity", "cancelled_quantity", "remaining_quantity"))
            if filled + cancelled + remaining != quantity or result["terminal"] is not (remaining == 0):
                raise EvidenceUnavailable("inconsistent fill/cancel/remaining quantities")
            executions = result.get("executions")
            if not isinstance(executions, list) or len(executions) > 2000:
                raise EvidenceUnavailable("execution evidence unavailable")
            seen = {}
            for execution in executions:
                if not isinstance(execution, dict) or set(execution) != {"execution_id", "quantity"}:
                    raise EvidenceUnavailable("malformed execution evidence")
                key, count = execution["execution_id"], _count(execution["quantity"], positive=True)
                if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9]{1,40}", key) is None:
                    raise EvidenceUnavailable("broker execution ID missing")
                if key in seen and seen[key] != count:
                    raise EvidenceUnavailable("conflicting duplicate execution")
                seen[key] = count
            if sum(seen.values()) != filled:
                raise EvidenceUnavailable("identified executions do not explain cumulative fill")
            result["executions"] = [dict(execution_id=key, quantity=seen[key]) for key in sorted(seen)]
            return result

    def _pages(self, entry, path, tr_id, params, output, suffix):
        """Read all pages or reject the whole inquiry, including early matches."""
        rows, cursors = [], set()
        params = dict(params)
        for page in range(self.max_pages):
            self._identity(entry)
            kwargs = {"appendHeaders": {"tr_cont": "N"}} if page else {}
            response = self.trader._request(path, tr_id, dict(params), **kwargs)
            self._identity(entry)
            if response.isOK() is not True:
                raise EvidenceUnavailable("KIS inquiry failed")
            body, header = response.getBody(), response.getHeader()
            missing = object()
            statuses = [_get(body, key, missing) for key in ("rt_cd", "RT_CD")]
            statuses = [value for value in statuses if value is not missing]
            if not statuses or any(type(value) is not str or value != "0" for value in statuses):
                raise EvidenceUnavailable("KIS business success unavailable")
            current = _history_output(body, output)
            if isinstance(current, dict): current = [current]
            if not isinstance(current, list) or not all(isinstance(row, dict) for row in current) or len(rows)+len(current) > 2000:
                raise EvidenceUnavailable("malformed or oversized KIS inquiry")
            rows.extend(current)
            continuation = _cursor_component(header, "tr_cont", "TR_CONT", None)
            if continuation in ("", "D", "E"): return rows
            if continuation not in ("F", "M"):
                raise EvidenceUnavailable("unknown KIS continuation status")
            fk, nk = f"CTX_AREA_FK{suffix}", f"CTX_AREA_NK{suffix}"
            cursor = (_cursor_component(body, fk.lower(), fk), _cursor_component(body, nk.lower(), nk))
            if (not all(isinstance(value, str) for value in cursor) or not any(cursor)
                    or any(len(value) > 256 for value in cursor) or cursor in cursors):
                raise EvidenceUnavailable("invalid or repeated continuation cursor")
            cursors.add(cursor); params[fk], params[nk] = cursor
        raise EvidenceUnavailable("KIS pagination limit reached")

    def _row_account(self, row):
        for keys, expected in (
            (("cano", "CANO"), self.trader.trenv.my_acct),
            (("acnt_prdt_cd", "ACNT_PRDT_CD"), self.trader.trenv.my_prod),
        ):
            if any(key in row for key in keys) and _first(row, *keys) != expected:
                raise EvidenceUnavailable("foreign or conflicting account in broker evidence")

    def _native_snapshot(self, entry):
        """Documented cash queries plus explicit reservation coverage provider.

        Real-only open-order APIs cannot be silently used in demo. Neither
        reservation date windows nor cancelable-only order lists prove all
        external orders; the coverage reader must explicitly supply that
        missing coverage or the gateway pauses before any SELL.
        """
        if self.trader.mode != "real":
            raise EvidenceUnavailable("native demo open-order coverage unsupported")
        if self.reservation_reader is None:
            raise EvidenceUnavailable("native all-order/reservation coverage unsupported without authoritative provider")
        coverage = self.reservation_reader(entry)
        if (not isinstance(coverage, dict) or coverage.get("complete") is not True
                or coverage.get("covers_all_order_kinds") is not True
                or not isinstance(coverage.get("open_orders"), list)):
            raise EvidenceUnavailable("all-order reservation coverage unsupported")
        identity = self._identity(entry)
        for key, value in zip(("market", "ticker", "account_scope"), identity):
            if coverage.get(key) != value: raise EvidenceUnavailable("order coverage identity mismatch")
        coverage_time = self._fresh(coverage.get("as_of"))
        started = self._clock()
        account, product = self.trader.trenv.my_acct, self.trader.trenv.my_prod
        common = dict(CANO=account, ACNT_PRDT_CD=product)
        holdings = {}
        if self.market == "KR":
            params = dict(common, AFHR_FLPR_YN="N", OFL_YN="", INQR_DVSN="02", UNPR_DVSN="01",
                          FUND_STTL_ICLD_YN="N", FNCG_AMT_AUTO_RDPT_YN="N", PRCS_DVSN="00",
                          CTX_AREA_FK100="", CTX_AREA_NK100="")
            rows = self._pages(entry, "/uapi/domestic-stock/v1/trading/inquire-balance", "TTTC8434R", params, "output1", 100)
            open_rows = self._pages(entry, "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl", "TTTC0084R",
                                    dict(common, INQR_DVSN_1="0", INQR_DVSN_2="0", CTX_AREA_FK100="", CTX_AREA_NK100=""), "output", 100)
            for row in rows:
                self._row_account(row)
                if not isinstance(row.get("pdno"), str) or re.fullmatch(r"[0-9]{6}", row["pdno"]) is None:
                    raise EvidenceUnavailable("unattributable KR holding row")
                if row.get("pdno") != identity[1]: continue
                if row.get("hldg_qty") in (None, "") or row.get("ord_psbl_qty") in (None, ""):
                    raise EvidenceUnavailable("holding/sellable evidence missing")
                value = (validated_balance_quantity(row["hldg_qty"]), validated_balance_quantity(row["ord_psbl_qty"]))
                if "KRX" in holdings and holdings["KRX"] != value: raise EvidenceUnavailable("conflicting holding rows")
                holdings["KRX"] = value
            quote = self.trader.get_current_price(identity[1])
            for row in open_rows: self._row_account(row)
            if any(not isinstance(row.get("pdno"), str) or re.fullmatch(r"[0-9]{6}", row["pdno"]) is None for row in open_rows):
                raise EvidenceUnavailable("unattributable KR open order")
            orders = [row for row in open_rows if row.get("pdno") == identity[1]]
            venue = None
        else:
            for exchange in ("NASD", "NYSE", "AMEX"):
                params = dict(common, OVRS_EXCG_CD=exchange, TR_CRCY_CD="USD", CTX_AREA_FK200="", CTX_AREA_NK200="")
                rows = self._pages(entry, "/uapi/overseas-stock/v1/trading/inquire-balance", "TTTS3012R", params, "output1", 200)
                for row in rows:
                    self._row_account(row)
                    if not isinstance(row.get("ovrs_pdno"), str) or re.fullmatch(r"[A-Z0-9.]{1,16}", row["ovrs_pdno"]) is None:
                        raise EvidenceUnavailable("unattributable US holding row")
                    if row.get("ovrs_pdno") != identity[1]: continue
                    venue = row.get("ovrs_excg_cd")
                    if venue != exchange:
                        raise EvidenceUnavailable("US holding row venue differs from queried exchange")
                    if venue not in ("NASD", "NYSE", "AMEX") or row.get("ovrs_cblc_qty") in (None, "") or row.get("ord_psbl_qty") in (None, ""):
                        raise EvidenceUnavailable("US holding/sellable/venue missing")
                    value = (validated_balance_quantity(row["ovrs_cblc_qty"]), validated_balance_quantity(row["ord_psbl_qty"]))
                    if venue in holdings and holdings[venue] != value: raise EvidenceUnavailable("conflicting holding rows")
                    holdings[venue] = value
            if len(holdings) != 1:
                raise EvidenceUnavailable("US holding venue unavailable or ambiguous")
            venue = next(iter(holdings))
            open_rows = self._pages(entry, "/uapi/overseas-stock/v1/trading/inquire-nccs", "TTTS3018R",
                                    dict(common, OVRS_EXCG_CD="NASD", SORT_SQN="DS", CTX_AREA_FK200="", CTX_AREA_NK200=""), "output", 200)
            for row in open_rows: self._row_account(row)
            if any(not isinstance(row.get("pdno"), str) or re.fullmatch(r"[A-Z0-9.]{1,16}", row["pdno"]) is None for row in open_rows):
                raise EvidenceUnavailable("unattributable US open order")
            orders = [row for row in open_rows if row.get("pdno") == identity[1]]
            quote = self.trader.get_current_price(identity[1], venue)
        if not isinstance(quote, dict): raise EvidenceUnavailable("quote inquiry unavailable")
        # Legacy US quote helpers can return yesterday's base price. Such a
        # fallback cannot establish a fresh trigger quote: require an explicit
        # provider timestamp instead of silently stamping the local clock.
        quote_time = self._fresh(quote.get("as_of"))
        holding, sellable = next(iter(holdings.values()), (0, 0))
        result = dict(zip(("market", "ticker", "account_scope"), identity))
        result.update(holding_quantity=holding, sellable_quantity=sellable,
                      current_price=quote.get("current_price"),
                      as_of=min(started, coverage_time, quote_time).isoformat(),
                      complete=True, open_orders=[*orders, *coverage["open_orders"]])
        if venue is not None: result["order_venue"] = venue
        return result

    def _native_order(self, entry, attempt):
        """Read dated SELL status; missing broker execution/cancel data pauses.

        Current official daily history examples expose cumulative quantities,
        not a guaranteed unique execution ID. Never synthesize execution IDs
        from a quantity delta, timestamp, order ID, or notification.
        """
        receipt = attempt["receipt"]
        date, number = _date(receipt.get("order_date")), _order_no(receipt.get("order_no"))
        common = dict(CANO=self.trader.trenv.my_acct, ACNT_PRDT_CD=self.trader.trenv.my_prod)
        if self.market == "KR":
            params = dict(common, INQR_STRT_DT=date, INQR_END_DT=date, SLL_BUY_DVSN_CD="01",
                          PDNO=_value(entry, "ticker"), CCLD_DVSN="00", INQR_DVSN="00", INQR_DVSN_3="00",
                          INQR_DVSN_1="", ORD_GNO_BRNO="", ODNO=number, CTX_AREA_FK100="", CTX_AREA_NK100="")
            rows = self._pages(entry, "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                               "TTTC0081R" if self.trader.mode == "real" else "VTTC0081R", params, "output1", 100)
        else:
            demo = self.trader.mode == "demo"
            params = dict(common, PDNO="" if demo else "%", ORD_STRT_DT=date, ORD_END_DT=date,
                          SLL_BUY_DVSN="00" if demo else "01", CCLD_NCCS_DVSN="00", SORT_SQN="DS",
                          ORD_DT="", ORD_GNO_BRNO="", ODNO="", OVRS_EXCG_CD="" if demo else "NASD",
                          CTX_AREA_FK200="", CTX_AREA_NK200="")
            rows = self._pages(entry, "/uapi/overseas-stock/v1/trading/inquire-ccnl",
                               "VTTS3035R" if demo else "TTTS3035R", params, "output", 200)
        matches = []
        for row in rows:
            if _first(row, "odno", "ODNO") != number: continue
            if _date(_first(row, "ord_dt", "ORD_DT")) != date: continue
            if (_first(row, "pdno", "PDNO", "ovrs_pdno", "OVRS_PDNO") != _value(entry, "ticker")
                    or _first(row, "sll_buy_dvsn_cd", "sll_buy_dvsn", "SLL_BUY_DVSN_CD", "SLL_BUY_DVSN") != "01"):
                raise EvidenceUnavailable("target SELL provenance conflict")
            for key, value in (("cano", self.trader.trenv.my_acct), ("acnt_prdt_cd", self.trader.trenv.my_prod)):
                if key in row and row[key] != value: raise EvidenceUnavailable("foreign account in SELL evidence")
            matches.append(row)
        if not matches: raise EvidenceUnavailable("SELL history missing; absence is not cancellation")
        # Preserve the query result's uncertainty. The documented native
        # schemas do not guarantee the execution identities required by the
        # durable book. A separate proven order reader must supply them.
        raise EvidenceUnavailable("native execution-ID and explicit cancellation proof unsupported")
