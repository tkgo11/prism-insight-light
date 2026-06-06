from __future__ import annotations

from fastapi import APIRouter, Request

from webui.services.readiness_service import get_config_status, get_readiness_summary

router = APIRouter(prefix="/readiness")


@router.get("")
def readiness_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "readiness.html",
        {"request": request, "readiness": get_readiness_summary(run_live_check=False)},
    )


@router.get("/api")
def readiness_api(live: bool = False):
    return get_readiness_summary(run_live_check=live)


@router.get("/config/api")
def config_api():
    return get_config_status()
