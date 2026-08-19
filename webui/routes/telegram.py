from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from webui.routes.guards import get_urlencoded_form, require_csrf_token
from webui.services import telegram_service

router = APIRouter(prefix="/telegram")


def _page_context(
    request: Request,
    *,
    channel: str = "",
    pages: int = 1,
    max_posts: int = 20,
    preview: dict | None = None,
) -> dict:
    return {
        "request": request,
        "csrf_token": request.app.state.settings.csrf_token,
        "channel": channel,
        "pages": pages,
        "max_posts": max_posts,
        "preview": preview,
    }


@router.get("")
def telegram_page(request: Request):
    templates = request.app.state.templates
    return templates.TemplateResponse(request, "telegram.html", _page_context(request))


@router.post("/preview", dependencies=[Depends(require_csrf_token)])
async def telegram_preview(request: Request):
    """Render bounded, read-only Telegram candidates for operator review."""

    templates = request.app.state.templates
    form = await get_urlencoded_form(request)
    channel = form.get("channel", "").strip()
    try:
        if len(channel) > 256:
            raise ValueError("Channel override must not exceed 256 characters")
        pages = min(5, max(1, int(form.get("pages", "1"))))
        max_posts = min(100, max(1, int(form.get("max_posts", "20"))))
        preview = telegram_service.preview_telegram(
            channel or None,
            pages=pages,
            max_posts=max_posts,
        )
    except ValueError as exc:
        pages = 1
        max_posts = 20
        preview = {
            "ok": False,
            "items": [],
            "error": str(exc) or "Pages and post limit must be whole numbers within the displayed limits.",
        }
    return templates.TemplateResponse(
        request,
        "telegram.html",
        _page_context(
            request,
            channel=channel,
            pages=pages,
            max_posts=max_posts,
            preview=preview,
        ),
    )


@router.post("/api", dependencies=[Depends(require_csrf_token)])
def telegram_api(
    channel: str | None = Query(default=None, max_length=256),
    pages: int = Query(default=1, ge=1, le=5),
    max_posts: int = Query(default=20, ge=1, le=100),
):
    """Retain the machine-readable preview endpoint for automated callers."""

    return telegram_service.preview_telegram(channel, pages=pages, max_posts=max_posts)
