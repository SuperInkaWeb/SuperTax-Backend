"""DTOs del módulo DataFilter: datasets, filtrado y descarga."""
from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from src.modules.datafilter.domain.filtering import Delimiter, FilterGroup, SortSpec
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
