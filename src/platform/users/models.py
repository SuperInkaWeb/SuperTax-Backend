"""
Modelo de usuario (Core).

La identidad se delega en Auth0 (`auth0_sub`). El rol NO vive aquí: el rol es
por empresa y se resuelve vía `memberships` (Modelo B). La única marca de nivel
plataforma es `is_platform_admin` (el SuperAdmin), que es la excepción al
modelo multi-empresa.
"""
import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.database.base import Base, utcnow


class UserStatus(str, enum.Enum):
    activo = "activo"
    inactivo = "inactivo"
    pendiente = "pendiente"


class User(Base):
    __tablename__ = "users"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(primary_key=True)
    auth0_sub: Mapped[str | None] = mapped_column(String(100), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    nombre: Mapped[str] = mapped_column(String(150))
    is_platform_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, name="user_status"), default=UserStatus.activo
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
