from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from webui.routes.guards import get_urlencoded_form, require_csrf_token
from webui.services.account_service import get_config_editor_model, list_accounts, update_config_fields
from webui.services.queue_service import summarize_queue
from webui.services.trade_service import dispatch_manual_order, trading_guard_status

router = APIRouter(prefix="/trading")


def _page_context(request: Request, *, trade_result=None, config_result=None) -> dict:
    return {
        "request": request,
        "accounts": list_accounts(),
        "queue": summarize_queue(),
        "config_model": get_config_editor_model(),
        "trade_guard": trading_guard_status(),
        "trade_result": trade_result,
        "config_result": config_result,
        "csrf_token": "local-webui",
    }


@router.get("")
def trading_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "trading.html", _page_context(request))


@router.post("/order", dependencies=[Depends(require_csrf_token)])
async def manual_order(request: Request):
    templates = request.app.state.templates
    form = await get_urlencoded_form(request)
    result = dispatch_manual_order(
        action=form.get("action", ""),
        ticker=form.get("ticker", ""),
        price=float(form.get("price") or 0),
        company_name=form.get("company_name", ""),
        market=form.get("market", "auto"),
        trading_mode=form.get("trading_mode") or None,
        arm_phrase=form.get("arm_phrase", ""),
        account_name=form.get("account_name", ""),
    )
    return templates.TemplateResponse(request, "trading.html", _page_context(request, trade_result=result))


@router.post("/config", dependencies=[Depends(require_csrf_token)])
async def update_config(request: Request):
    templates = request.app.state.templates
    form = await get_urlencoded_form(request)
    fields = {
        "default_mode": form.get("default_mode", ""),
        "auto_trading": form.get("auto_trading", ""),
        "default_unit_amount": form.get("default_unit_amount", ""),
        "default_unit_amount_usd": form.get("default_unit_amount_usd", ""),
        "default_unit_asset_percent": form.get("default_unit_asset_percent", ""),
        "default_unit_asset_percent_usd": form.get("default_unit_asset_percent_usd", ""),
        "auto_exchange_usd_on_buy": form.get("auto_exchange_usd_on_buy", ""),
        "max_auto_exchange_krw": form.get("max_auto_exchange_krw", ""),
        "auto_exchange_min_shortfall_usd": form.get("auto_exchange_min_shortfall_usd", ""),
    }
    result = update_config_fields(
        fields,
        {
            "name": form.get("signal_strategy_name", ""),
            "split_count": form.get("signal_strategy_split_count", "2"),
        },
    )
    return templates.TemplateResponse(request, "trading.html", _page_context(request, config_result=result))


@router.get("/accounts/api")
def accounts_api():
    return list_accounts()


@router.get("/guard/api")
def guard_api():
    return trading_guard_status()
