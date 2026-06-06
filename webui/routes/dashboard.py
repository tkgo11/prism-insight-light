from __future__ import annotations

from fastapi import APIRouter, Request

from webui.services.log_service import get_known_log_paths
from webui.services.queue_service import summarize_queue
from webui.services.readiness_service import get_readiness_summary

router = APIRouter()


@router.get("/")
def dashboard(request: Request):
    templates = request.app.state.templates
    readiness = get_readiness_summary(run_live_check=False)
    queue = summarize_queue()
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "request": request,
            "readiness": readiness,
            "queue": queue,
            "logs": get_known_log_paths(),
            "settings": request.app.state.settings,
        },
    )
