from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from webui.routes.guards import get_urlencoded_form, require_csrf_token
from webui.services.signal_service import parse_signal_input, parse_signal_text

router = APIRouter(prefix="/signals")


class SignalRequest(BaseModel):
    payload: dict[str, Any] | str


def _page_context(
    request: Request,
    *,
    payload: str = "",
    validation_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "request": request,
        "csrf_token": request.app.state.settings.csrf_token,
        "payload": payload,
        "validation_result": validation_result,
    }


@router.get("")
def signals_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "signals.html", _page_context(request))


@router.post("/validate-form", dependencies=[Depends(require_csrf_token)])
async def validate_signal_form(request: Request):
    """Render an operator-friendly validation result for JSON or Telegram text."""

    templates = request.app.state.templates
    form = await get_urlencoded_form(request)
    payload = form.get("payload", "").strip()
    if not payload:
        validation_result = {
            "ok": False,
            "signal": None,
            "error": "Paste a JSON signal or Telegram-style message before validating.",
        }
    else:
        validation_result = parse_signal_text(payload)
    return templates.TemplateResponse(
        request,
        "signals.html",
        _page_context(
            request,
            payload=payload,
            validation_result=validation_result,
        ),
    )


@router.post("/validate", dependencies=[Depends(require_csrf_token)])
def validate_signal(body: SignalRequest):
    """Keep the JSON API available for automation and external operator tooling."""

    if isinstance(body.payload, str):
        return parse_signal_text(body.payload)
    return parse_signal_input(body.payload)
