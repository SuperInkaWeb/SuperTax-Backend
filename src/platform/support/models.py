"""
Modelos de soporte (tickets) — schema `core`.

Un ticket pertenece a una empresa y lo abre un usuario; el hilo de mensajes
distingue quién es soporte (plataforma) de quién es el cliente. Es una función
transversal de la plataforma, no de un módulo.
"""
import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.database.base import Base, utcnow


class TicketStatus(str, enum.Enum):
    abierto = "abierto"       # esperando respuesta de soporte
    respondido = "respondido" # soporte respondió, espera al cliente
    cerrado = "cerrado"


class TicketModel(Base):
    __tablename__ = "tickets"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("core.users.id"))
    asunto: Mapped[str] = mapped_column(String(200))
    status: Mapped[TicketStatus] = mapped_column(
        Enum(TicketStatus, name="ticket_status"), default=TicketStatus.abierto, index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class TicketMessageModel(Base):
    __tablename__ = "ticket_messages"
    __table_args__ = {"schema": "core"}

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[int] = mapped_column(
        ForeignKey("core.tickets.id", ondelete="CASCADE"), index=True
    )
    author_id: Mapped[int] = mapped_column(ForeignKey("core.users.id"))
    es_soporte: Mapped[bool] = mapped_column(Boolean, default=False)
    mensaje: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
