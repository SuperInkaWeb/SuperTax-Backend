"""Schemas Pydantic del módulo Scanner."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentoItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    tipo_documento: str
    tipo_etiqueta: str | None = None
    confianza: float | None = None
    nombre_archivo: str
    campos: dict


class ActualizarCamposInput(BaseModel):
    campos: dict
