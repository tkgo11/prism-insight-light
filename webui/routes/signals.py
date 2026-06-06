from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from webui.routes.guards import require_csrf_token
from webui.services.signal_service import parse_signal_input, parse_signal_text

router = APIRouter(prefix="/signals")


class SignalRequest(BaseModel):
    payload: dict[str, Any] | str


@router.get("")
def signals_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "signals.html", {"request": request})


@router.post("/validate", dependencies=[Depends(require_csrf_token)])
def validate_signal(body: SignalRequest):
    if isinstance(body.payload, str):
        return parse_signal_text(body.payload)
    return parse_signal_input(body.payload)
