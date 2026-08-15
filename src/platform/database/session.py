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

# pool_pre_ping: valida (y reconecta) la conexión al sacarla del pool.
# pool_recycle: recicla conexiones ociosas > 5 min (Neon cierra las inactivas).
engine = create_engine(
    settings.DATABASE_URL, pool_pre_ping=True, pool_recycle=300, future=True
)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, class_=Session
)


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
