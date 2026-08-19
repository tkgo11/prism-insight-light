from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from webui.routes.guards import get_urlencoded_form, require_csrf_token
from webui.services.dry_run_service import simulate_dispatch

router = APIRouter(prefix="/dry-run")


class DryRunRequest(BaseModel):
    payload: dict[str, Any]


def _page_context(
    request: Request,
    *,
    payload: str = "",
    simulation_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "request": request,
        "csrf_token": request.app.state.settings.csrf_token,
        "payload": payload,
        "simulation_result": simulation_result,
    }


@router.get("")
def dry_run_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "dry_run.html", _page_context(request))


@router.post("/simulate-form", dependencies=[Depends(require_csrf_token)])
async def simulate_form(request: Request):
    """Render a safe dry-run result without touching dispatch or queue services."""

    templates = request.app.state.templates
    form = await get_urlencoded_form(request)
    payload = form.get("payload", "").strip()
    if not payload:
        simulation_result = {
            "ok": False,
            "result": None,
            "signal": None,
            "error": "Paste a JSON signal before running a simulation.",
        }
    else:
        try:
            decoded_payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            simulation_result = {
                "ok": False,
                "result": None,
                "signal": None,
                "error": f"Invalid JSON: {exc.msg}",
            }
        else:
            simulation_result = simulate_dispatch(decoded_payload)
    return templates.TemplateResponse(
        request,
        "dry_run.html",
        _page_context(
            request,
            payload=payload,
            simulation_result=simulation_result,
        ),
    )


@router.post("/simulate", dependencies=[Depends(require_csrf_token)])
def simulate(body: DryRunRequest):
    """Retain the JSON simulator endpoint for automated tooling."""

    return simulate_dispatch(body.payload)
