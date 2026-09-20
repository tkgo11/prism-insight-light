from __future__ import annotations

import re

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from webui.services.log_service import get_known_log_paths, tail_log

router = APIRouter(prefix="/logs")
LEVELS = ("ALL", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def filtered_log(name: str, lines: int, q: str, level: str):
    log = tail_log(name, max_lines=lines)
    original = len(log["lines"])
    log["lines"] = [line for line in log["lines"] if q.casefold() in line.casefold()
                    and (level == "ALL" or re.search(r"\b" + level + r"\b", line, re.IGNORECASE))]
    return dict(log, scanned_count=original, matched_count=len(log["lines"]))


@router.get("")
def logs_page(request: Request, name: str = "subscriber", lines: int = Query(100, ge=20, le=500),
              q: str = Query("", max_length=200), level: str = Query("ALL", pattern="^(ALL|DEBUG|INFO|WARNING|ERROR|CRITICAL)$")):
    return request.app.state.templates.TemplateResponse(request, "logs.html", {
        "request": request, "log": filtered_log(name, lines, q, level),
        "sources": get_known_log_paths(), "levels": LEVELS, "selected_lines": lines, "q": q, "level": level,
    })


@router.get("/api")
def logs_api(name: str = "subscriber", lines: int = Query(100, ge=20, le=500),
             q: str = Query("", max_length=200), level: str = Query("ALL", pattern="^(ALL|DEBUG|INFO|WARNING|ERROR|CRITICAL)$")):
    return filtered_log(name, lines, q, level)


@router.get("/download")
def logs_download(name: str = "subscriber", lines: int = Query(100, ge=20, le=500),
                  q: str = Query("", max_length=200), level: str = Query("ALL", pattern="^(ALL|DEBUG|INFO|WARNING|ERROR|CRITICAL)$")):
    log = filtered_log(name, lines, q, level)
    if not log["ok"]:
        return PlainTextResponse(log["error"], status_code=400)
    return PlainTextResponse("\n".join(log["lines"]), headers={
        "Content-Disposition": 'attachment; filename="prism-masked-log.txt"',
    })
