"""
Modelos de autorización y entitlements (Core).

- Roles y permisos (RBAC): un rol agrupa permisos; la membresía asigna un rol
  al usuario dentro de una empresa. Los permisos son strings namespaced por
  módulo (p. ej. "sire.job.create"), así agregar un módulo = agregar permisos
  sin tocar el motor de autorización.
- Módulos y entitlements: `company_modules` define qué módulos tiene contratada
  cada empresa (la doble puerta: primero el módulo, luego el permiso).
"""
import enum
from datetime import date

from sqlalchemy import Column, Date, Enum, ForeignKey, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.platform.database.base import Base


class RoleScope(str, enum.Enum):
    platform = "platform"
    company = "company"


role_permissions = Table(
    "role_permissions",
    Base.metadata,
    Column("role_id", ForeignKey("core.roles.id", ondelete="CASCADE"), primary_key=True),
    Column(
        "permission_id",
        ForeignKey("core.permissions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    schema="core",
)


class Permission(Base):
    __tablename__ = "permissions"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    descripcion: Mapped[str] = mapped_column(String(200), default="")


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(100))
    scope: Mapped[RoleScope] = mapped_column(
        Enum(RoleScope, name="role_scope"), default=RoleScope.company
    )
    permissions: Mapped[list[Permission]] = relationship(
        secondary=role_permissions, lazy="selectin"
    )


class Module(Base):
    __tablename__ = "modules"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(100))


class CompanyModuleStatus(str, enum.Enum):
    activo = "activo"
    suspendido = "suspendido"


class CompanyModule(Base):
    __tablename__ = "company_modules"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    module_key: Mapped[str] = mapped_column(String(50), index=True)
    status: Mapped[CompanyModuleStatus] = mapped_column(
        Enum(CompanyModuleStatus, name="company_module_status"),
        default=CompanyModuleStatus.activo,
    )
    fecha_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    fecha_fin: Mapped[date | None] = mapped_column(Date, nullable=True)
