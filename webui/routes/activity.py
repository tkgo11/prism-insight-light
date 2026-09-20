from fastapi import APIRouter, Request

from webui.services.activity_service import summarize_activity

router = APIRouter(prefix="/activity")


@router.get("")
def activity_page(request: Request):
    return request.app.state.templates.TemplateResponse(
        request, "activity.html", {"request": request, "activity": summarize_activity()}
    )


@router.get("/api")
def activity_api():
    return summarize_activity()
