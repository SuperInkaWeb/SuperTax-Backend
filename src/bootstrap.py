"""
Preparación de la base de datos al arrancar: migraciones + seed idempotente.

En producción esto lo haría el pre-deploy de la plataforma de despliegue; hacerlo
también al arrancar vuelve el sistema AUTO-REPARABLE: si el pre-deploy no corre, la
app igual deja el esquema y el catálogo de módulos al día.

Seguridad operativa:
  - Idempotente: la migración no se re-aplica si ya está en `head`; el seed solo
    hace upserts (nunca borra ni duplica), así que repetirlo no cambia nada.
  - Un solo ejecutor: se toma un `pg_advisory_lock` de Postgres, de modo que aunque
    haya varias réplicas, solo UNA migra/siembra a la vez (las demás lo omiten).
  - No fatal: ante cualquier fallo, la API arranca igual y solo se registra el error.
  - Desactivable: `DB_BOOTSTRAP_ON_STARTUP=false` si prefieres migrar solo en un
    paso de despliegue dedicado.

Vive en `src` (nivel de composición, junto a `seed`/`main`), no en `platform`,
porque el seed conoce los módulos registrados y el núcleo no debe depender de ellos.
"""
import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

from src.platform.config.settings import settings
from src.platform.database.session import SessionLocal, engine
from src.seed import seed

_log = logging.getLogger("platform.bootstrap")
# Raíz del repo (…/plataforma-backend): bootstrap.py está en src/.
_ROOT = Path(__file__).resolve().parents[1]
# Clave arbitraria (pero fija) del advisory lock que serializa el arranque entre réplicas.
_LOCK_KEY = 736_421_90


def _migrar() -> None:
    """Aplica las migraciones pendientes hasta `head` (usa settings.DATABASE_URL)."""
    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "migrations"))
    command.upgrade(cfg, "head")


def _migrar_y_sembrar() -> None:
    _migrar()
    _log.info("Migraciones aplicadas (o ya estaban al día).")
    db = SessionLocal()
    try:
        seed(db)
        _log.info("Seed idempotente completado (roles + catálogo de módulos + permisos).")
    finally:
        db.close()


def ensure_database_ready() -> None:
    """Migra el esquema y siembra roles/módulos/permisos, una sola instancia a la vez."""
    if not settings.DB_BOOTSTRAP_ON_STARTUP:
        return
    try:
        # Solo la instancia que gane el lock migra/siembra; las demás lo omiten (no
        # esperan): evita que varias réplicas corran Alembic/seed en paralelo.
        with engine.connect() as conn:
            adquirido = conn.execute(
                text("SELECT pg_try_advisory_lock(:k)"), {"k": _LOCK_KEY}
            ).scalar()
            if not adquirido:
                _log.info("Otra instancia está preparando la BD; se omite en esta.")
                return
            try:
                _migrar_y_sembrar()
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _LOCK_KEY})
                conn.commit()
    except Exception:
        _log.exception("La preparación de la BD al arrancar falló (la API arranca igual)")
