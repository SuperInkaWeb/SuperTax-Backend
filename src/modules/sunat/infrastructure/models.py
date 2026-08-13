"""
Modelos de persistencia del módulo SUNAT (schema `sunat`).

Referencian a `core.companies`/`core.users` solo por id. Los secretos (usuario y
clave SOL, tokens de Google Drive) se guardan cifrados con Fernet.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.database.base import Base, utcnow


class SunatCredentialsModel(Base):
    """Credenciales SOL de la empresa para el login web de SUNAT (Playwright)."""

    __tablename__ = "sunat_credentials"
    __table_args__ = {"schema": "sunat"}

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), unique=True
    )
    ruc: Mapped[str] = mapped_column(String(11))
    usuario_enc: Mapped[str] = mapped_column(Text)
    clave_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.users.id"), nullable=True
    )


class DriveTokenModel(Base):
    """Token OAuth de Google Drive de la empresa (destino de las descargas)."""

    __tablename__ = "drive_tokens"
    __table_args__ = {"schema": "sunat"}

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), unique=True
    )
    access_token_enc: Mapped[str] = mapped_column(Text, default="")
    refresh_token_enc: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class JobResultModel(Base):
    """Historial del resultado de un job de descarga."""

    __tablename__ = "job_results"
    __table_args__ = {"schema": "sunat"}

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("core.users.id"))
    resultados: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
