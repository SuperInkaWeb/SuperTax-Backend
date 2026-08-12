"""
Manejo uniforme de errores.

Convierte cualquier excepción en la respuesta estándar de la plataforma, sin
exponer stack traces al cliente (regla de seguridad OWASP A05). Los errores no
controlados se registran en el log del servidor, no se devuelven al usuario.
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("plataforma")


def _payload(message: str, errors: list[str] | None = None) -> dict:
    return {"status": "error", "data": None, "message": message, "errors": errors or []}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc(request: Request, exc: StarletteHTTPException):
        return JSONResponse(status_code=exc.status_code, content=_payload(str(exc.detail)))

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        errores = [
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422, content=_payload("Datos inválidos", errores)
        )

    @app.exception_handler(Exception)
    async def _unhandled_exc(request: Request, exc: Exception):
        logger.exception("Error no controlado en %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500, content=_payload("Error interno del servidor")
        )
