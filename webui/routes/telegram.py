from __future__ import annotations

from fastapi import APIRouter, Request

from webui.services import telegram_service

router = APIRouter(prefix="/telegram")


@router.get("")
def telegram_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "telegram.html", {"request": request, "preview": None})


@router.get("/api")
def telegram_api(channel: str | None = None, pages: int = 1, max_posts: int = 20):
    return telegram_service.preview_telegram(channel, pages=pages, max_posts=max_posts)
