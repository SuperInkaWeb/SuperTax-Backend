"""
Motor y sesión de base de datos.

Expone `get_db`, la dependencia de FastAPI que entrega una sesión por request
y garantiza su cierre. Los repositorios de cada módulo la reciben por inyección
(Dependency Injection), nunca crean su propia conexión.
"""
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.platform.config.settings import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, class_=Session
)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
