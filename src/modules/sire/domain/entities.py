"""
Dominio del módulo SIRE (puro, sin frameworks).

`ReconciliationJob` representa una conciliación entre el libro de la empresa y la
propuesta de SUNAT. Aquí solo viven la entidad y sus estados; la persistencia y
los clientes externos son detalles de infraestructura.
"""
import enum
from dataclasses import dataclass
from datetime import datetime


class TipoLibro(str, enum.Enum):
    compras = "compras"
    ventas = "ventas"


class JobStatus(str, enum.Enum):
    en_cola = "en_cola"
    procesando = "procesando"
    completado = "completado"
    error = "error"


@dataclass
class ReconciliationJob:
    id: int
    company_id: int
    periodo: str
    tipo_libro: TipoLibro
    status: JobStatus
    created_at: datetime
    empresa_filename: str | None = None
    completed_at: datetime | None = None
    error_message: str | None = None
    # Resumen del resultado (presente solo cuando el job terminó).
    igv_diferencia_total: float | None = None
    tiene_alertas_rojas: bool | None = None
