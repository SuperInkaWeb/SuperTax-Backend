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


class Estudio(Base):
    """Grupo contable: el inquilino dueño de una cartera de empresas.

    Sirve igual para un contador independiente (estudio de un solo miembro) que
    para un estudio con equipo. `owner_user_id` es el admin principal / creador
    (ancla para el traspaso); los demás admins y miembros se modelan aparte.
    """

    __tablename__ = "estudios"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    owner_user_id: Mapped[int] = mapped_column(
        ForeignKey("core.users.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class EstudioMemberStatus(str, enum.Enum):
    activo = "activo"
    inactivo = "inactivo"


class EstudioMember(Base):
    """Miembro del equipo de un estudio, con un rol a nivel estudio.

    Un usuario puede pertenecer a varios estudios (contador en varios estudios).
    El rol `admin_empresa` aquí = admin del estudio: ve y opera TODAS sus empresas.
    Los roles no-admin se restringen a clientes concretos vía `memberships`.
    """

    __tablename__ = "estudio_members"
    __table_args__ = (
        UniqueConstraint("estudio_id", "user_id", name="uq_estudio_member"),
        {"schema": "core"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    estudio_id: Mapped[int] = mapped_column(
        ForeignKey("core.estudios.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("core.users.id", ondelete="CASCADE"), index=True
    )
    role_id: Mapped[int] = mapped_column(ForeignKey("core.roles.id"), index=True)
    status: Mapped[EstudioMemberStatus] = mapped_column(
        Enum(EstudioMemberStatus, name="estudio_member_status"),
        default=EstudioMemberStatus.activo,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanyStatus(str, enum.Enum):
    activo = "activo"
    inactivo = "inactivo"


class Company(Base):
    __tablename__ = "companies"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(primary_key=True)
    ruc: Mapped[str] = mapped_column(String(11), unique=True, index=True)
    razon_social: Mapped[str] = mapped_column(String(200))
    # Estudio dueño de la empresa. Nullable a propósito (patrón expand): la
    # migración rellena los existentes; se forzará NOT NULL tras verificar prod.
    estudio_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.estudios.id", ondelete="RESTRICT"), index=True, nullable=True
    )
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
