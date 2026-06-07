from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request

from webui.routes.guards import require_csrf_token
from webui.services.account_service import get_config_editor_model, list_accounts, update_config_fields
from webui.services.queue_service import summarize_queue
from webui.services.trade_service import dispatch_manual_order, trading_guard_status

router = APIRouter(prefix="/trading")


@router.get("")
def trading_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "trading.html",
        {
            "request": request,
            "accounts": list_accounts(),
            "queue": summarize_queue(),
            "config_model": get_config_editor_model(),
            "trade_guard": trading_guard_status(),
            "trade_result": None,
            "config_result": None,
            "csrf_token": "local-webui",
        },
    )


@router.post("/order", dependencies=[Depends(require_csrf_token)])
def manual_order(
    request: Request,
    action: str = Form(...),
    ticker: str = Form(...),
    price: float = Form(...),
    company_name: str = Form(""),
    market: str = Form("auto"),
    trading_mode: str = Form(""),
    arm_phrase: str = Form(""),
    account_name: str = Form(""),
):
    templates = request.app.state.templates
    result = dispatch_manual_order(
        action=action,
        ticker=ticker,
        price=price,
        company_name=company_name,
        market=market,
        trading_mode=trading_mode or None,
        arm_phrase=arm_phrase,
        account_name=account_name,
    )
    return templates.TemplateResponse(
        request,
        "trading.html",
        {
            "request": request,
            "accounts": list_accounts(),
            "queue": summarize_queue(),
            "config_model": get_config_editor_model(),
            "trade_guard": trading_guard_status(),
            "trade_result": result,
            "config_result": None,
            "csrf_token": "local-webui",
        },
    )


@router.post("/config", dependencies=[Depends(require_csrf_token)])
def update_config(
    request: Request,
    x_webui_csrf: str = Form("local-webui"),
    default_mode: str = Form(""),
    auto_trading: str = Form(""),
    default_unit_amount: str = Form(""),
    default_unit_amount_usd: str = Form(""),
    default_unit_asset_percent: str = Form(""),
    default_unit_asset_percent_usd: str = Form(""),
    auto_exchange_usd_on_buy: str = Form(""),
    max_auto_exchange_krw: str = Form(""),
    auto_exchange_min_shortfall_usd: str = Form(""),
    signal_strategy_name: str = Form(""),
    signal_strategy_split_count: str = Form("2"),
):
    templates = request.app.state.templates
    fields = {
        "default_mode": default_mode,
        "auto_trading": auto_trading,
        "default_unit_amount": default_unit_amount,
        "default_unit_amount_usd": default_unit_amount_usd,
        "default_unit_asset_percent": default_unit_asset_percent,
        "default_unit_asset_percent_usd": default_unit_asset_percent_usd,
        "auto_exchange_usd_on_buy": auto_exchange_usd_on_buy,
        "max_auto_exchange_krw": max_auto_exchange_krw,
        "auto_exchange_min_shortfall_usd": auto_exchange_min_shortfall_usd,
    }
    result = update_config_fields(fields, {"name": signal_strategy_name, "split_count": signal_strategy_split_count})
    return templates.TemplateResponse(
        request,
        "trading.html",
        {
            "request": request,
            "accounts": list_accounts(),
            "queue": summarize_queue(),
            "config_model": get_config_editor_model(),
            "trade_guard": trading_guard_status(),
            "trade_result": None,
            "config_result": result,
            "csrf_token": "local-webui",
        },
    )


@router.get("/accounts/api")
def accounts_api():
    return list_accounts()


@router.get("/guard/api")
def guard_api():
    return trading_guard_status()
