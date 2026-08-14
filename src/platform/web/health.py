"""
Endpoints de salud.

- `/health`        liveness: el proceso responde (sin tocar infraestructura).
- `/health/ready`  readiness: la base de datos está accesible. Devuelve 503 si
  falla, para que orquestadores (Railway/K8s) no enruten tráfico a una instancia
  que aún no está lista.
"""
from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.platform.config.settings import settings
from src.platform.database.session import get_db

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": settings.APP_NAME, "env": settings.ENV}


@router.get("/health/ready")
def readiness(response: Response, db: Session = Depends(get_db)) -> dict:
    checks = {"database": _check_db(db)}
    todo_ok = all(checks.values())
    if not todo_ok:
        response.status_code = 503
    return {"status": "ok" if todo_ok else "degraded", "checks": checks}


def _check_db(db: Session) -> bool:
    try:
        db.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
