from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from webui.routes.guards import require_csrf_token
from webui.services.dry_run_service import simulate_dispatch

router = APIRouter(prefix="/dry-run")


class DryRunRequest(BaseModel):
    payload: dict[str, Any]


@router.get("")
def dry_run_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "dry_run.html",
        {"request": request, "csrf_token": request.app.state.settings.csrf_token},
    )


@router.post("/simulate", dependencies=[Depends(require_csrf_token)])
def simulate(body: DryRunRequest):
    return simulate_dispatch(body.payload)
