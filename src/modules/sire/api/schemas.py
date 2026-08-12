"""Schemas Pydantic de entrada/salida del módulo SIRE."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.modules.sire.domain.entities import JobStatus, TipoLibro


class JobResponse(BaseModel):
    # from_attributes: se construye leyendo la entidad de dominio (dataclass).
    model_config = ConfigDict(from_attributes=True)

    id: int
    periodo: str
    tipo_libro: TipoLibro
    status: JobStatus
    empresa_filename: str | None = None
    created_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    igv_diferencia_total: float | None = None
    tiene_alertas_rojas: bool | None = None
    has_report: bool = False
    has_csv_a: bool = False
    has_csv_b: bool = False
    has_csv_c: bool = False
    has_csv_d: bool = False


class CredentialsInput(BaseModel):
    usuario_sol: str = Field(min_length=1, max_length=50)
    clave_sol: str = Field(min_length=1)
    client_id: str = Field(min_length=1, max_length=100)
    client_secret: str = Field(min_length=1)


class CredentialsStatusResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    configured: bool
    usuario_sol: str | None = None
    client_id: str | None = None
    updated_at: datetime | None = None
