"""
Punto de entrada de la Plataforma (monolito modular).

Compone la aplicación en orden:
  1. Middleware transversal (CORS, request-id).
  2. Manejo uniforme de errores.
  3. Endpoints de salud del núcleo.
  4. Routers de los módulos registrados (vacío en Fase 0; se llenan en Fases 2-4).

Cada módulo se monta bajo `/api/<key>` de forma automática.
"""
import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.bootstrap import ensure_database_ready
from src.module_registry import MODULES
from src.modules.sire.infrastructure.tipo_cambio.sincronizador import capturar_tc_hoy
from src.platform.authorization.roles_api import router as roles_router
from src.platform.config.settings import settings
from src.platform.database.session import SessionLocal
from src.platform.onboarding.api import router as onboarding_router
from src.platform.support.api import router as tickets_router
from src.platform.tenancy.companies_api import router as companies_router
from src.platform.tenancy.company_access_api import router as company_access_router
from src.platform.tenancy.estudio_api import router as estudio_router
from src.platform.tenancy.members_api import router as members_router
from src.platform.tenancy.my_companies_api import router as my_companies_router
from src.platform.tenancy.team_api import router as team_router
from src.platform.web.errors import register_error_handlers
from src.platform.web.health import router as health_router
from src.platform.web.me import router as me_router
from src.platform.web.middleware import register_middleware

# Routers del Core (administración de plataforma): identidad, tenencia, onboarding.
_CORE_ROUTERS = (
    me_router,
    onboarding_router,
    companies_router,
    my_companies_router,
    members_router,
    estudio_router,
    company_access_router,
    team_router,
    roles_router,
    tickets_router,
)


_log = logging.getLogger("platform.startup")

# Captura diaria del tipo de cambio desde el txt oficial de SUNAT (ver sincronizador).
_TC_INTERVALO_SEGUNDOS = 24 * 60 * 60


def _capturar_tc_hoy() -> None:
    db = SessionLocal()
    try:
        capturar_tc_hoy(db)
    finally:
        db.close()


async def _bucle_tc_diario() -> None:
    """Baja el TC del día de SUNAT al arrancar y cada 24h. Best-effort: un fallo se
    registra y se reintenta; mantiene la tabla al día sin intervención manual."""
    while True:
        try:
            await asyncio.to_thread(_capturar_tc_hoy)
        except Exception:
            _log.warning("Captura diaria de TC falló", exc_info=True)
        await asyncio.sleep(_TC_INTERVALO_SEGUNDOS)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Al arrancar: asegura el esquema y el catálogo de módulos (migración + seed
    idempotentes), y deja que cada módulo recupere su estado (jobs interrumpidos /
    encolados). Un fallo aquí no debe impedir que la API sirva salud/tráfico."""
    ensure_database_ready()
    for module in MODULES:
        if module.on_startup is None:
            continue
        try:
            module.on_startup()
        except Exception:
            _log.exception("on_startup del módulo '%s' falló", module.key)

    tc_task = asyncio.create_task(_bucle_tc_diario())
    try:
        yield
    finally:
        tc_task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG, lifespan=_lifespan)

    register_middleware(app)
    register_error_handlers(app)
    app.include_router(health_router)
    for router in _CORE_ROUTERS:
        app.include_router(router)

    for module in MODULES:
        app.include_router(module.router, prefix=f"/api/{module.key}")

    return app


app = create_app()
