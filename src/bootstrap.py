"""
Preparación de la base de datos al arrancar: migraciones + seed idempotente.

En producción esto lo haría el pre-deploy de la plataforma de despliegue; hacerlo
también al arrancar vuelve el sistema AUTO-REPARABLE: si el pre-deploy no corre, la
app igual deja el esquema y el catálogo de módulos al día. Ambos pasos son
idempotentes (no duplican ni borran) y NO fatales: ante un fallo, la API arranca
igual y solo se registra el error (no se cae el servicio).

Vive en `src` (nivel de composición, junto a `seed`/`main`), no en `platform`,
porque el seed conoce los módulos registrados y el núcleo no debe depender de ellos.
"""
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

from src.platform.database.session import SessionLocal
from src.seed import seed

_log = logging.getLogger("platform.bootstrap")
# Raíz del repo (…/plataforma-backend): bootstrap.py está en src/.
_ROOT = Path(__file__).resolve().parents[1]


def _migrar() -> None:
    """Aplica las migraciones pendientes hasta `head` (usa settings.DATABASE_URL)."""
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    command.upgrade(cfg, "head")


def ensure_database_ready() -> None:
    """Migra el esquema y siembra roles/módulos/permisos. Idempotente y tolerante a fallos."""
    try:
        _migrar()
        _log.info("Migraciones aplicadas (o ya estaban al día).")
    except Exception:
        _log.exception("No se pudieron aplicar las migraciones al arrancar")

    db = SessionLocal()
    try:
        seed(db)
        _log.info("Seed idempotente completado (roles + catálogo de módulos + permisos).")
    except Exception:
        _log.exception("El seed de arranque falló")
    finally:
        db.close()
