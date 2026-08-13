"""Modelo de solicitud de acceso (onboarding) — Core."""
import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.database.base import Base, utcnow


class AccessRequestStatus(str, enum.Enum):
    pendiente = "pendiente"
    aprobado = "aprobado"
    rechazado = "rechazado"


class AccessRequest(Base):
    __tablename__ = "access_requests"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    nombre: Mapped[str] = mapped_column(String(150))
    empresa_nombre: Mapped[str] = mapped_column(String(200))
    ruc: Mapped[str] = mapped_column(String(11))
    telefono: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mensaje: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AccessRequestStatus] = mapped_column(
        Enum(AccessRequestStatus, name="access_request_status"),
        default=AccessRequestStatus.pendiente,
    )
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.users.id"), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
