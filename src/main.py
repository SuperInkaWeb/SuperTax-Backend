"""
Punto de entrada de la Plataforma (monolito modular).

Compone la aplicación en orden:
  1. Middleware transversal (CORS, request-id).
  2. Manejo uniforme de errores.
  3. Endpoints de salud del núcleo.
  4. Routers de los módulos registrados (vacío en Fase 0; se llenan en Fases 2-4).

Cada módulo se monta bajo `/api/<key>` de forma automática.
"""
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.module_registry import MODULES
from src.platform.authorization.roles_api import router as roles_router
from src.platform.config.settings import settings
from src.platform.onboarding.api import router as onboarding_router
from src.platform.support.api import router as tickets_router
from src.platform.tenancy.companies_api import router as companies_router
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
    team_router,
    roles_router,
    tickets_router,
)


_log = logging.getLogger("platform.startup")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Al arrancar, deja que cada módulo recupere su estado (jobs interrumpidos /
    encolados). Un fallo aquí no debe impedir que la API sirva salud/tráfico."""
    for module in MODULES:
        if module.on_startup is None:
            continue
        try:
            module.on_startup()
        except Exception:
            _log.exception("on_startup del módulo '%s' falló", module.key)
    yield


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
