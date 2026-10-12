"""DTOs del módulo DataFilter: resumen de dataset para listados."""
from datetime import datetime

from pydantic import BaseModel

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
