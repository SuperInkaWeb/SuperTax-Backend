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
    escenario_a_count: int | None = None
    escenario_b_count: int | None = None
    escenario_c_count: int | None = None
    escenario_d_count: int | None = None
    # Origen de la propuesta SUNAT usada (para mostrar reúso).
    propuesta_origen_at: datetime | None = None
    # Disponibilidad de archivos generados.
    has_report: bool = False
    has_csv_a: bool = False
    has_csv_b: bool = False
    has_csv_c: bool = False
    has_csv_d: bool = False
