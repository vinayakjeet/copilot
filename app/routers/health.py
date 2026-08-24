from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request):
    state = request.app.state
    return {
        "status": "ok" if state.readiness.ready else "warming",
        "uptime_s": round(state.readiness.snapshot()["uptime_s"], 1),
    }
