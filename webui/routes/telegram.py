from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from webui.routes.guards import require_csrf_token
from webui.services import telegram_service

router = APIRouter(prefix="/telegram")


@router.get("")
def telegram_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "telegram.html", {"request": request, "preview": None})


@router.post("/api", dependencies=[Depends(require_csrf_token)])
def telegram_api(
    channel: str | None = Query(default=None, max_length=256),
    pages: int = Query(default=1, ge=1, le=5),
    max_posts: int = Query(default=20, ge=1, le=100),
):
    return telegram_service.preview_telegram(channel, pages=pages, max_posts=max_posts)
