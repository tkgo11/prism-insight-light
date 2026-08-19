from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status

from webui.routes.guards import (
    get_urlencoded_form,
    require_csrf_token,
    require_local_mutation,
)
from webui.services.account_service import get_config_editor_model, update_config_fields
from webui.services.readiness_service import get_config_status, get_readiness_summary

router = APIRouter(prefix="/readiness")

_EDITABLE_FIELDS = (
    "default_mode",
    "multi_account_trading_enabled",
    "auto_trading",
    "default_unit_amount",
    "default_unit_amount_usd",
    "default_unit_asset_percent",
    "default_unit_asset_percent_usd",
    "auto_exchange_usd_on_buy",
    "max_auto_exchange_krw",
    "auto_exchange_min_shortfall_usd",
)


def _page_context(
    request: Request,
    *,
    readiness: dict | None = None,
    config_result: dict | None = None,
) -> dict:
    config_model = get_config_editor_model()
    if request.app.state.network_read_only:
        config_model = config_model | {"writable": False}
    return {
        "request": request,
        "readiness": readiness or get_readiness_summary(run_live_check=False),
        "config_model": config_model,
        "config_result": config_result,
        "csrf_token": request.app.state.settings.csrf_token,
    }


@router.get("")
def readiness_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "readiness.html", _page_context(request))


@router.post("/probe-form", dependencies=[Depends(require_csrf_token)])
def readiness_probe_form(request: Request):
    """Run the existing explicit live probe and render its masked result."""

    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "readiness.html",
        _page_context(request, readiness=get_readiness_summary(run_live_check=True)),
    )


@router.post(
    "/config",
    dependencies=[Depends(require_local_mutation), Depends(require_csrf_token)],
)
async def update_config(request: Request):
    """Save the existing allowlisted operational configuration from System."""

    templates = request.app.state.templates
    form = await get_urlencoded_form(request)
    fields = {name: form[name] for name in _EDITABLE_FIELDS if name in form}
    strategy_fields = {
        "signal_strategy_name": "name",
        "signal_strategy_split_count": "split_count",
    }
    strategy = {
        target: form[source]
        for source, target in strategy_fields.items()
        if source in form
    }
    try:
        config_result = update_config_fields(fields, strategy or None)
        response_status = status.HTTP_200_OK
    except (OSError, TypeError, ValueError) as exc:
        config_result = {
            "ok": False,
            "path_label": None,
            "error": f"Invalid configuration: {exc}",
        }
        response_status = status.HTTP_400_BAD_REQUEST
    return templates.TemplateResponse(
        request,
        "readiness.html",
        _page_context(request, config_result=config_result),
        status_code=response_status,
    )


@router.get("/api")
def readiness_api():
    return get_readiness_summary(run_live_check=False)


@router.post("/probe", dependencies=[Depends(require_csrf_token)])
def readiness_probe():
    return get_readiness_summary(run_live_check=True)


@router.get("/config/api")
def config_api():
    return get_config_status()
