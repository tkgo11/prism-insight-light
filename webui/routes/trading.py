from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Request, status

from webui.routes.guards import (
    get_urlencoded_form,
    require_csrf_token,
    require_local_mutation,
)
from webui.services.account_service import get_config_editor_model, list_accounts, update_config_fields
from webui.services.queue_service import summarize_queue
from webui.services.trade_service import (
    dispatch_manual_order,
    live_trading_enabled,
    trading_guard_status,
)

router = APIRouter(prefix="/trading")


def _trade_guard(request: Request) -> dict:
    settings = request.app.state.settings
    guard = trading_guard_status(force_dry_run=settings.force_dry_run)
    if not request.app.state.network_read_only:
        return guard
    return guard | {
        "enabled": False,
        "message": (
            "Non-loopback WebUI access is diagnostic and read-only; "
            "broker orders and configuration changes are blocked."
        ),
    }


def _page_context(request: Request, *, trade_result=None, config_result=None) -> dict:
    settings = request.app.state.settings
    config_model = get_config_editor_model()
    trade_guard = _trade_guard(request)
    if request.app.state.network_read_only:
        config_model = config_model | {"writable": False}
    return {
        "request": request,
        "accounts": list_accounts(),
        "queue": summarize_queue(settings.queue_path),
        "config_model": config_model,
        "trade_guard": trade_guard,
        "trade_result": trade_result,
        "config_result": config_result,
        "csrf_token": request.app.state.settings.csrf_token,
        "order_nonce": (
            None
            if request.app.state.network_read_only
            else request.app.state.order_nonces.issue()
        ),
    }


@router.get("")
def trading_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "trading.html", _page_context(request))


@router.post(
    "/order",
    dependencies=[Depends(require_local_mutation), Depends(require_csrf_token)],
)
async def manual_order(request: Request):
    templates = request.app.state.templates
    form = await get_urlencoded_form(request)
    try:
        price = float(form.get("price") or "")
        if not math.isfinite(price) or price <= 0:
            raise ValueError("price must be a finite positive number")
        if (
            live_trading_enabled()
            and not request.app.state.settings.force_dry_run
            and not request.app.state.order_nonces.consume(form.get("order_nonce", ""))
        ):
            return templates.TemplateResponse(
                request,
                "trading.html",
                _page_context(
                    request,
                    trade_result={
                        "ok": False,
                        "blocked": True,
                        "signal": None,
                        "result": None,
                        "error": (
                            "This order ticket is expired or was already submitted. "
                            "Review the result before creating a new order."
                        ),
                    },
                ),
                status_code=status.HTTP_409_CONFLICT,
            )
        result = await dispatch_manual_order(
            action=form.get("action", ""),
            ticker=form.get("ticker", ""),
            price=price,
            company_name=form.get("company_name", ""),
            market=form.get("market", "auto"),
            trading_mode=form.get("trading_mode") or None,
            arm_phrase=form.get("arm_phrase", ""),
            account_name=form.get("account_name", ""),
            force_dry_run=request.app.state.settings.force_dry_run,
            queue_path=request.app.state.settings.queue_path,
            work_tracker=request.app.state.work_tracker,
        )
        response_status = status.HTTP_200_OK
    except (TypeError, ValueError) as exc:
        result = {
            "ok": False,
            "blocked": True,
            "signal": None,
            "result": None,
            "error": f"Invalid order: {exc}",
        }
        response_status = status.HTTP_400_BAD_REQUEST
    return templates.TemplateResponse(
        request,
        "trading.html",
        _page_context(request, trade_result=result),
        status_code=response_status,
    )


@router.post(
    "/config",
    dependencies=[Depends(require_local_mutation), Depends(require_csrf_token)],
)
async def update_config(request: Request):
    templates = request.app.state.templates
    form = await get_urlencoded_form(request)
    editable_fields = (
        "default_mode",
        "auto_trading",
        "default_unit_amount",
        "default_unit_amount_usd",
        "default_unit_asset_percent",
        "default_unit_asset_percent_usd",
        "auto_exchange_usd_on_buy",
        "max_auto_exchange_krw",
        "auto_exchange_min_shortfall_usd",
    )
    fields = {name: form[name] for name in editable_fields if name in form}
    strategy_form_fields = {
        "signal_strategy_name": "name",
        "signal_strategy_split_count": "split_count",
    }
    strategy = {
        target: form[source]
        for source, target in strategy_form_fields.items()
        if source in form
    }
    try:
        result = update_config_fields(
            fields,
            strategy or None,
        )
        response_status = status.HTTP_200_OK
    except (OSError, TypeError, ValueError) as exc:
        result = {"ok": False, "path_label": None, "error": f"Invalid configuration: {exc}"}
        response_status = status.HTTP_400_BAD_REQUEST
    return templates.TemplateResponse(
        request,
        "trading.html",
        _page_context(request, config_result=result),
        status_code=response_status,
    )


@router.get("/accounts/api")
def accounts_api():
    return list_accounts()


@router.get("/guard/api")
def guard_api(request: Request):
    return _trade_guard(request)
