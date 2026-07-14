from __future__ import annotations

from fastapi import APIRouter, Request

from webui.services.queue_service import summarize_queue

router = APIRouter(prefix="/queue")


@router.get("")
def queue_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(
        request,
        "queue.html",
        {"request": request, "queue": summarize_queue(request.app.state.settings.queue_path)},
    )


@router.get("/api")
def queue_api(request: Request):
    return summarize_queue(request.app.state.settings.queue_path)
