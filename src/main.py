"""
Punto de entrada de la Plataforma (monolito modular).

Compone la aplicación en orden:
  1. Middleware transversal (CORS, request-id).
  2. Manejo uniforme de errores.
  3. Endpoints de salud del núcleo.
  4. Routers de los módulos registrados (vacío en Fase 0; se llenan en Fases 2-4).

Cada módulo se monta bajo `/api/<key>` de forma automática.
"""
from fastapi import FastAPI

from src.module_registry import MODULES
from src.platform.config.settings import settings
from src.platform.web.errors import register_error_handlers
from src.platform.web.health import router as health_router
from src.platform.web.me import router as me_router
from src.platform.web.middleware import register_middleware


def create_app() -> FastAPI:
    app = FastAPI(title=settings.APP_NAME, debug=settings.DEBUG)

    register_middleware(app)
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(me_router)

    for module in MODULES:
        app.include_router(module.router, prefix=f"/api/{module.key}")

    return app


app = create_app()
