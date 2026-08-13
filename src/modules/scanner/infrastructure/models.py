"""
Modelos de persistencia del módulo Scanner (schema `scanner`).

Un documento escaneado guarda su clasificación (tipo), la confianza del OCR, el
archivo en storage y los campos extraídos (`campos`, JSON editable). Referencia a
`core.companies`/`core.users` solo por id.
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.database.base import Base, utcnow


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
