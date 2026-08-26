"""
Modelos de persistencia del módulo Scanner (schema `scanner`).

Un documento escaneado guarda su clasificación (tipo), la confianza del OCR, el
archivo en storage y los campos extraídos (`campos`, JSON editable). Referencia a
`core.companies`/`core.users` solo por id.

La extracción por OCR es CPU-intensiva: se procesa en una cola (`scanner_jobs`)
que consume un worker aparte, no en la petición HTTP.
"""
import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.database.base import Base, utcnow


class ScannerJobStatus(str, enum.Enum):
    en_cola = "en_cola"
    procesando = "procesando"
    completado = "completado"
    error = "error"


class ScannerJobModel(Base):
    """Trabajo de extracción encolado: un archivo subido pendiente de OCR."""

    __tablename__ = "scanner_jobs"
    __table_args__ = {"schema": "scanner"}

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("core.users.id"))
    nombre_archivo: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    # Tipo elegido por el usuario (salta la auto-clasificación). None = automático.
    tipo_forzado: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[ScannerJobStatus] = mapped_column(
        Enum(ScannerJobStatus, name="scanner_job_status"),
        default=ScannerJobStatus.en_cola,
        index=True,
    )
    documento_id: Mapped[int | None] = mapped_column(
        ForeignKey("scanner.documentos.id", ondelete="SET NULL"), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class DocumentoModel(Base):
    __tablename__ = "documentos"
    __table_args__ = {"schema": "scanner"}

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("core.users.id"))
    tipo_documento: Mapped[str] = mapped_column(String(50))
    tipo_etiqueta: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confianza: Mapped[float | None] = mapped_column(Float, nullable=True)
    nombre_archivo: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    campos: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
