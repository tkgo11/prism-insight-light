from __future__ import annotations

from fastapi import APIRouter, Request

from webui.services.log_service import tail_log

router = APIRouter(prefix="/logs")


@router.get("")
def logs_page(request: Request, name: str = "subscriber"):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "logs.html", {"request": request, "log": tail_log(name)})


@router.get("/api")
def logs_api(name: str = "subscriber"):
    return tail_log(name)
