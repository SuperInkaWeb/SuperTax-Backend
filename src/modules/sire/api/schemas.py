"""Schemas Pydantic de entrada/salida del módulo SIRE."""
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from src.modules.sire.domain.entities import JobStatus, TipoLibro


class JobResponse(BaseModel):
    # from_attributes: se construye leyendo la entidad de dominio (dataclass).
    model_config = ConfigDict(from_attributes=True)

    id: int
    periodo: str
    tipo_libro: TipoLibro
    status: JobStatus
    created_at: datetime
    completed_at: datetime | None = None
    error_message: str | None = None
    igv_diferencia_total: float | None = None
    tiene_alertas_rojas: bool | None = None
