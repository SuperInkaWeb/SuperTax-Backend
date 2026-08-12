"""Base declarativa única para todos los modelos (núcleo Core + módulos)."""
from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Timestamp UTC con zona horaria (fuente única para campos de auditoría)."""
    return datetime.now(timezone.utc)
