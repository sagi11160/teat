from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text

from .. import __version__
from ..db import get_engine
from .deps import Services, services

router = APIRouter(tags=["ops"])


@router.get("/healthz")
def healthz():
    return {"status": "ok", "version": __version__}


@router.get("/readyz")
def readyz(svc: Services = Depends(services)):
    checks: dict[str, str] = {}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    checks["supervisor"] = "ok" if svc.supervisor.is_running() else "stopped"
    ok = all(v == "ok" for v in checks.values())
    return JSONResponse(
        {"status": "ready" if ok else "not_ready", "checks": checks, "active_bridges": len(svc.supervisor.running_link_ids())},
        status_code=200 if ok else 503,
    )


@router.get("/api/config")
def public_config(svc: Services = Depends(services)):
    return {
        "providers": [{"id": a.provider.value, "name": a.display_name} for a in svc.registry.enabled()],
        "allow_registration": svc.settings.allow_registration,
        "version": __version__,
    }
