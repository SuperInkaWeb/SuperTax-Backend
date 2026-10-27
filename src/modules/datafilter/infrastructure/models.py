"""
Modelos de persistencia del módulo DataFilter (schema `datafilter`).

Un *dataset* es un archivo grande que el usuario sube; se convierte a Parquet
(en un subproceso) y queda listo para filtrar, pivotear y descargar con DuckDB.
Aquí vive solo la metadata (estado, columnas, conteo); el Parquet y el archivo
original viven en storage (R2). Referencia a `core.companies`/`core.users` por id.

El estado y el origen se guardan como texto (los valores de los enums de abajo)
para no manejar tipos ENUM de Postgres en las migraciones.
"""
import enum
from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.database.base import Base, utcnow


class DatasetStatus(str, enum.Enum):
    uploading = "uploading"    # subida directa a R2 en curso (multipart), aún sin archivo
    pending = "pending"        # subido, aún no procesado
    processing = "processing"  # convirtiéndose a Parquet
    ready = "ready"            # listo para filtrar/descargar
    failed = "failed"          # error de ingesta


class DatasetOrigin(str, enum.Enum):
    """Cómo se creó el dataset (separa las vistas del frontend)."""
    uploaded = "uploaded"   # subido por el usuario
    pivot = "pivot"         # generado por una tabla dinámica
    computed = "computed"   # generado al añadir columnas calculadas
    replaced = "replaced"   # generado por un buscar-y-reemplazar por columna
    deduped = "deduped"     # generado al eliminar filas duplicadas


class DatasetModel(Base):
    """Metadata de un dataset subido y su estado de ingesta."""

    __tablename__ = "datasets"
    __table_args__ = {"schema": "datafilter"}

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("core.users.id"))
    original_filename: Mapped[str] = mapped_column(String(255))
    extension: Mapped[str] = mapped_column(String(20), default="")
    status: Mapped[str] = mapped_column(
        String(20), default=DatasetStatus.pending.value, index=True
    )
    origin: Mapped[str] = mapped_column(String(20), default=DatasetOrigin.uploaded.value)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    row_count: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Esquema de columnas [{"name": ..., "type": ...}] y hojas (si es Excel multi-hoja).
    columns: Mapped[list] = mapped_column(JSON, default=list)
    sheets: Mapped[list] = mapped_column(JSON, default=list)
    active_sheet: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Rutas en storage: el Parquet listo y el archivo original subido.
    parquet_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # UploadId de la subida multipart en curso (directo a R2); null cuando no hay una.
    # Text (no String(255)): el UploadId de R2 supera los 255 caracteres.
    multipart_upload_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
