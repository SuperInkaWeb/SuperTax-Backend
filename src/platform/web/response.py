"""
Envelope de respuesta consistente para toda la API.

Estructura estándar `{ status, data, message, errors }` para que el frontend
consuma todos los módulos de la misma forma.
"""
from typing import Any

from pydantic import BaseModel


class ApiResponse(BaseModel):
    status: str = "ok"
    data: Any | None = None
    message: str | None = None
    errors: list[str] = []
