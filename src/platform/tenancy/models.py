"""
Modelos de tenencia (Core) — Modelo B: usuario ↔ empresas (N:M).

`Membership` es la tabla puente que habilita que un usuario (p. ej. un contador
de un estudio) pertenezca a varias empresas, con un rol distinto en cada una.
Es la pieza central del multi-tenant de la plataforma.
"""
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.database.base import Base, utcnow


class CompanyStatus(str, enum.Enum):
    activo = "activo"
    inactivo = "inactivo"


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(primary_key=True)
    ruc: Mapped[str] = mapped_column(String(11), unique=True, index=True)
    razon_social: Mapped[str] = mapped_column(String(200))
    status: Mapped[CompanyStatus] = mapped_column(
        Enum(CompanyStatus, name="company_status"), default=CompanyStatus.activo
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class MembershipStatus(str, enum.Enum):
    activo = "activo"
    inactivo = "inactivo"


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_membership_user_company"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), index=True
    )
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[int] = mapped_column(ForeignKey("core.roles.id"), index=True)
    status: Mapped[MembershipStatus] = mapped_column(
        Enum(MembershipStatus, name="membership_status"), default=MembershipStatus.activo
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
