"""
Casos de uso de soporte (tickets).

Reglas de acceso:
- Soporte = administrador de plataforma (`is_platform_admin`): ve y responde todos.
- Cliente = miembro de la empresa del ticket: ve y responde los de su empresa.
- `es_soporte` de cada mensaje se marca según quién lo escribe.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.support.models import TicketMessageModel, TicketModel, TicketStatus
from src.platform.tenancy.models import Membership
from src.platform.users.models import User


class SupportError(Exception):
    """Error de negocio de soporte (se traduce a 4xx en la API)."""


def _company_ids_de(db: Session, user_id: int) -> list[int]:
    return list(
        db.scalars(select(Membership.company_id).where(Membership.user_id == user_id)).all()
    )


def puede_acceder(db: Session, user: User, ticket: TicketModel) -> bool:
    if user.is_platform_admin:
        return True
    return db.scalar(
        select(Membership.id).where(
            Membership.user_id == user.id, Membership.company_id == ticket.company_id
        )
    ) is not None


def crear_ticket(
    db: Session, user: User, company_id: int, asunto: str, mensaje: str
) -> TicketModel:
    ticket = TicketModel(company_id=company_id, created_by_id=user.id, asunto=asunto)
    db.add(ticket)
    db.flush()
    db.add(
        TicketMessageModel(
            ticket_id=ticket.id,
            author_id=user.id,
            es_soporte=user.is_platform_admin,
            mensaje=mensaje,
        )
    )
    db.commit()
    db.refresh(ticket)
    return ticket


def listar_tickets(db: Session, user: User) -> list[TicketModel]:
    query = select(TicketModel).order_by(TicketModel.updated_at.desc())
    if not user.is_platform_admin:
        company_ids = _company_ids_de(db, user.id)
        if not company_ids:
            return []
        query = query.where(TicketModel.company_id.in_(company_ids))
    return list(db.scalars(query).all())


def obtener_ticket(db: Session, ticket_id: int) -> TicketModel | None:
    return db.get(TicketModel, ticket_id)


def mensajes_de(db: Session, ticket_id: int) -> list[TicketMessageModel]:
    return list(
        db.scalars(
            select(TicketMessageModel)
            .where(TicketMessageModel.ticket_id == ticket_id)
            .order_by(TicketMessageModel.id)
        ).all()
    )


def responder(db: Session, user: User, ticket: TicketModel, mensaje: str) -> None:
    if ticket.status == TicketStatus.cerrado:
        raise SupportError("El ticket está cerrado")
    db.add(
        TicketMessageModel(
            ticket_id=ticket.id,
            author_id=user.id,
            es_soporte=user.is_platform_admin,
            mensaje=mensaje,
        )
    )
    # Soporte responde → espera al cliente; el cliente responde → espera a soporte.
    ticket.status = TicketStatus.respondido if user.is_platform_admin else TicketStatus.abierto
    db.commit()


def cerrar(db: Session, ticket: TicketModel) -> None:
    ticket.status = TicketStatus.cerrado
    db.commit()
