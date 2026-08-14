"""Schemas Pydantic del módulo Scanner."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.modules.scanner.infrastructure.models import ScannerJobStatus


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


class ScannerJobCreated(BaseModel):
    """Respuesta al encolar un documento (subida)."""

    job_id: int
    status: ScannerJobStatus


class ScannerJobStatusResponse(BaseModel):
    """Estado de un job para el polling del frontend."""

    id: int
    status: ScannerJobStatus
    error_message: str | None = None
    documento: DocumentoItem | None = None
