from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from webui.routes.guards import require_csrf_token
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
def readiness_api():
    return get_readiness_summary(run_live_check=False)


@router.post("/probe", dependencies=[Depends(require_csrf_token)])
def readiness_probe():
    return get_readiness_summary(run_live_check=True)


@router.get("/config/api")
def config_api():
    return get_config_status()
