"""Schemas Pydantic del módulo SUNAT."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.modules.sunat.infrastructure.models import SunatJobStatus


class CredentialsInput(BaseModel):
    ruc: str = Field(min_length=11, max_length=11)
    usuario: str = Field(min_length=1, max_length=50)
    # Vacío = conservar la clave ya guardada (actualización parcial).
    clave: str = ""


class CredentialsStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    configured: bool
    ruc: str | None = None
    usuario: str | None = None


class SunatJobListItem(BaseModel):
    """Item del historial: estado del job + si tiene resultados para abrir."""

    job_id: str
    status: SunatJobStatus
    created_at: datetime
    completed_at: datetime | None = None
    has_result: bool


class JobResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: str
    created_at: datetime


class JobResultDetailResponse(BaseModel):
    job_id: str
    created_at: datetime
    resultados: list


class DriveStatusResponse(BaseModel):
    connected: bool
