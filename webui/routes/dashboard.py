from __future__ import annotations

from fastapi import APIRouter, Request

from webui.services.account_service import get_config_editor_model, list_accounts
from webui.services.log_service import get_known_log_paths
from webui.services.queue_service import summarize_queue
from webui.services.readiness_service import get_readiness_summary
from webui.services.trade_service import trading_guard_status

router = APIRouter()


@router.get("/")
def dashboard(request: Request):
    templates = request.app.state.templates
    readiness = get_readiness_summary(run_live_check=False)
    settings = request.app.state.settings
    queue = summarize_queue(settings.queue_path)
    accounts = list_accounts()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "readiness": readiness,
            "queue": queue,
            "logs": get_known_log_paths(),
            "settings": request.app.state.settings,
            "accounts": accounts,
            "config_model": get_config_editor_model(),
            "trade_guard": trading_guard_status(force_dry_run=settings.force_dry_run),
            "csrf_token": request.app.state.settings.csrf_token,
        },
    )
