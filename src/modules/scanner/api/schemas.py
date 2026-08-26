"""Schemas Pydantic del módulo Scanner."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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


class DocumentosExportInput(BaseModel):
    """Filas ya aplanadas por el frontend (cada una con 'archivo' + campos),
    columnas a exportar y etiquetas legibles. El backend arma el .xlsx."""

    filas: list[dict[str, Any]] = Field(default_factory=list)
    columnas: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)


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
