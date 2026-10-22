"""
Modelo de la bóveda de credenciales de plataforma (`core.company_credentials`).

Una fila por (empresa, clase de credencial). Es la fuente única compartida entre
módulos: las credenciales SOL las usan tanto el login web de SUNAT como la API de
SIRE, por lo que viven en el núcleo y no duplicadas en cada módulo. La lógica de
acceso (cifrado, actualización parcial, fallback) está en `vault`.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.database.base import Base, utcnow


class CompanyCredentialModel(Base):
    __tablename__ = "company_credentials"
    __table_args__ = (
        UniqueConstraint("company_id", "kind", name="uq_company_credential_kind"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    # Clase de credencial: "sol" (usuario+clave SOL) | "sire_api" (client_id+secret).
    kind: Mapped[str] = mapped_column(String(20))
    # Identificador no sensible (usuario SOL / client_id): se expone para prellenar.
    identifier: Mapped[str] = mapped_column(String(100))
    # Secreto cifrado con Fernet (clave SOL / client_secret): nunca se devuelve en claro.
    secret_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.users.id"), nullable=True
    )
