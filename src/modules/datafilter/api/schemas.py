"""DTOs del módulo DataFilter: datasets, filtrado, descarga y análisis."""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from src.modules.datafilter.domain.analysis import PivotSpec, ReplacementRule
from src.modules.datafilter.domain.expressions import ComputedColumn
from src.modules.datafilter.domain.filtering import (
    Delimiter,
    FilterGroup,
    SortDirection,
    SortSpec,
)
from src.modules.datafilter.infrastructure.models import DatasetModel


class ColumnInfo(BaseModel):
    name: str
    type: str


class DatasetSummary(BaseModel):
    """Vista resumida de un dataset (no expone rutas internas de storage)."""
    id: int
    original_filename: str
    status: str
    origin: str
    row_count: int | None = None
    size_bytes: int
    created_at: datetime
    error: str | None = None

    @classmethod
    def from_model(cls, m: DatasetModel) -> "DatasetSummary":
        return cls(
            id=m.id,
            original_filename=m.original_filename,
            status=m.status,
            origin=m.origin,
            row_count=m.row_count,
            size_bytes=m.size_bytes,
            created_at=m.created_at,
            error=m.error,
        )


class FilterRequest(BaseModel):
    """Base de las peticiones que filtran: árbol de filtros + proyección + orden."""
    filter: FilterGroup | None = None   # None o grupo vacío = sin filtro (todas las filas)
    select: list[str] = Field(default_factory=list)   # columnas a devolver; vacío = todas
    sort: list[SortSpec] = Field(default_factory=list)


class PreviewRequest(FilterRequest):
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
    # Búsqueda global: texto que debe aparecer en cualquier columna (insensible).
    search: str | None = Field(default=None, max_length=200)


class PreviewResponse(BaseModel):
    columns: list[str]
    rows: list[dict]
    total_matched: int
    limit: int
    offset: int


class DownloadFormat(str, Enum):
    CSV = "csv"
    XLSX = "xlsx"
    TXT = "txt"


class DownloadRequest(FilterRequest):
    format: DownloadFormat = DownloadFormat.CSV
    delimiter: Delimiter = Delimiter.TAB   # separador para TXT; se ignora en CSV/XLSX
    search: str | None = Field(default=None, max_length=200)


class DistinctValuesResponse(BaseModel):
    """Valores únicos de una columna para el filtro tipo Excel (casillas)."""
    column: str
    values: list[str]
    truncated: bool   # true si hay más valores de los devueltos


class SheetSelection(BaseModel):
    """Hoja a usar al re-ingestar un Excel multi-hoja."""
    sheet: str = Field(min_length=1, max_length=100)


class DatasetDetail(DatasetSummary):
    """Detalle con el esquema de columnas y las hojas (si es Excel multi-hoja)."""
    columns: list[ColumnInfo] = []
    sheets: list[str] = []
    active_sheet: str | None = None

    @classmethod
    def from_model(cls, m: DatasetModel) -> "DatasetDetail":
        return cls(
            id=m.id,
            original_filename=m.original_filename,
            status=m.status,
            origin=m.origin,
            row_count=m.row_count,
            size_bytes=m.size_bytes,
            created_at=m.created_at,
            error=m.error,
            columns=[ColumnInfo(**c) for c in (m.columns or [])],
            sheets=list(m.sheets or []),
            active_sheet=m.active_sheet,
        )


# ── Análisis: columnas calculadas, buscar/reemplazar, dedupe y estadísticas ──

class SortablePreview(BaseModel):
    """Orden opcional de una vista paginada: por una columna del RESULTADO.

    La columna se valida contra las columnas reales del resultado (whitelist) antes
    de tocar la BD; None = orden natural."""
    order_column: str | None = None
    order_direction: SortDirection = SortDirection.ASC


class AnalysisDownload(BaseModel):
    """Campos comunes a toda descarga de análisis (CSV/XLSX/TXT)."""
    format: DownloadFormat = DownloadFormat.CSV
    delimiter: Delimiter = Delimiter.TAB  # solo aplica a TXT


class ComputeSpec(BaseModel):
    """Definición de columnas calculadas (sin paginación ni destino)."""
    filter: FilterGroup | None = None
    columns: list[ComputedColumn] = Field(min_length=1, max_length=20)


class ComputeRequest(ComputeSpec, SortablePreview):
    """Columnas calculadas para VER (una página)."""
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class ComputeSaveRequest(ComputeSpec):
    """Columnas calculadas que se persisten como un dataset nuevo."""
    name: str = Field(min_length=1, max_length=120)


class ComputeDownloadRequest(ComputeSpec, AnalysisDownload):
    """Columnas calculadas que se descargan como archivo."""


class ReplaceSpec(BaseModel):
    """Definición de un buscar-y-reemplazar por columna (sin paginación ni destino)."""
    filter: FilterGroup | None = None
    replacements: list[ReplacementRule] = Field(min_length=1, max_length=20)


class ReplaceRequest(ReplaceSpec, SortablePreview):
    """Buscar y reemplazar para VER (una página, ya corregida)."""
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class ReplaceSaveRequest(ReplaceSpec):
    """Correcciones que se persisten como un dataset nuevo."""
    name: str = Field(min_length=1, max_length=120)


class ReplaceDownloadRequest(ReplaceSpec, AnalysisDownload):
    """Correcciones que se descargan como archivo."""


class DedupeSpec(BaseModel):
    """Definición de un 'eliminar duplicados' (sin paginación ni destino).

    `key_columns` vacío = se comparan TODAS las columnas (filas idénticas); con
    columnas, se conserva la primera fila de cada combinación."""
    filter: FilterGroup | None = None
    key_columns: list[str] = Field(default_factory=list, max_length=50)


class DedupeRequest(DedupeSpec, SortablePreview):
    """Eliminar duplicados para VER (una página del resultado)."""
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class DedupeResponse(BaseModel):
    columns: list[str]
    rows: list[dict]
    total_matched: int   # filas únicas resultantes
    total_original: int  # filas antes de quitar duplicados (con el filtro aplicado)
    limit: int
    offset: int


class DedupeSaveRequest(DedupeSpec):
    """Resultado sin duplicados que se persiste como un dataset nuevo."""
    name: str = Field(min_length=1, max_length=120)


class DedupeDownloadRequest(DedupeSpec, AnalysisDownload):
    """Resultado sin duplicados que se descarga como archivo."""


class PivotRequest(PivotSpec):
    """Pivote para VER (una página del reporte)."""
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)


class PivotResponse(BaseModel):
    columns: list[str]
    rows: list[dict]
    total_matched: int  # nº de filas agrupadas del reporte completo
    limit: int
    offset: int
    # Fila de Total general (métricas sobre todas las filas), sin las columnas de
    # agrupación. Solo para la vista previa; no se persiste.
    totals: dict | None = None


class PivotSaveRequest(PivotSpec):
    """Pivote que se persiste como un dataset nuevo (reporte reutilizable)."""
    name: str = Field(min_length=1, max_length=120)


class PivotDownloadRequest(PivotSpec, AnalysisDownload):
    """Pivote que se descarga como archivo."""


class StatsRequest(BaseModel):
    """Estadísticas descriptivas de UNA columna (respetando el filtro)."""
    filter: FilterGroup | None = None
    column: str


class TopValue(BaseModel):
    """Un valor frecuente de la columna y cuántas veces aparece."""
    value: str
    count: int


class ColumnStats(BaseModel):
    """Perfil descriptivo de una columna."""
    column: str
    is_numeric: bool
    total: int       # filas consideradas (con el filtro)
    non_null: int
    nulls: int
    distinct: int
    minimum: str | float | int | None
    maximum: str | float | int | None
    total_sum: float | None = None  # solo columnas numéricas
    average: float | None = None    # solo columnas numéricas
    top_values: list[TopValue]
