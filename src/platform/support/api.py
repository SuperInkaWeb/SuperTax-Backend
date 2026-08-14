"""Endpoints de soporte (tickets)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.support import service
from src.platform.support.models import TicketModel, TicketStatus
from src.platform.tenancy.current_tenant import ActiveContext, get_active_context
from src.platform.tenancy.models import Company
from src.platform.users.models import User

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


class CrearTicketInput(BaseModel):
    asunto: str = Field(min_length=3, max_length=200)
    mensaje: str = Field(min_length=3)


class ResponderInput(BaseModel):
    mensaje: str = Field(min_length=1)


class MensajeItem(BaseModel):
    id: int
    author_nombre: str
    es_soporte: bool
    mensaje: str
    created_at: datetime


class TicketListItem(BaseModel):
    id: int
    asunto: str
    status: TicketStatus
    company_nombre: str | None
    created_by_nombre: str
    created_at: datetime
    updated_at: datetime


class TicketDetail(BaseModel):
    id: int
    asunto: str
    status: TicketStatus
    company_nombre: str | None
    created_by_nombre: str
    created_at: datetime
    mensajes: list[MensajeItem]


def _nombre_usuario(db: Session, uid: int) -> str:
    user = db.get(User, uid)
    return user.nombre if user else "—"


def _nombre_empresa(db: Session, cid: int) -> str | None:
    company = db.get(Company, cid)
    return company.razon_social if company else None


def _detail(db: Session, ticket: TicketModel) -> TicketDetail:
    mensajes = service.mensajes_de(db, ticket.id)
    return TicketDetail(
        id=ticket.id,
        asunto=ticket.asunto,
        status=ticket.status,
        company_nombre=_nombre_empresa(db, ticket.company_id),
        created_by_nombre=_nombre_usuario(db, ticket.created_by_id),
        created_at=ticket.created_at,
        mensajes=[
            MensajeItem(
                id=m.id,
                author_nombre=_nombre_usuario(db, m.author_id),
                es_soporte=m.es_soporte,
                mensaje=m.mensaje,
                created_at=m.created_at,
            )
            for m in mensajes
        ],
    )


def _ticket_accesible(db: Session, user: User, ticket_id: int) -> TicketModel:
    ticket = service.obtener_ticket(db, ticket_id)
    if ticket is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ticket no encontrado")
    if not service.puede_acceder(db, user, ticket):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Sin acceso a este ticket")
    return ticket


@router.post("", response_model=TicketDetail, status_code=status.HTTP_201_CREATED)
def crear_ticket(
    payload: CrearTicketInput,
    ctx: ActiveContext = Depends(get_active_context),
    db: Session = Depends(get_db),
) -> TicketDetail:
    ticket = service.crear_ticket(db, ctx.user, ctx.company.id, payload.asunto, payload.mensaje)
    return _detail(db, ticket)


@router.get("", response_model=list[TicketListItem])
def listar_tickets(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[TicketListItem]:
    tickets = service.listar_tickets(db, user)
    return [
        TicketListItem(
            id=t.id,
            asunto=t.asunto,
            status=t.status,
            company_nombre=_nombre_empresa(db, t.company_id),
            created_by_nombre=_nombre_usuario(db, t.created_by_id),
            created_at=t.created_at,
            updated_at=t.updated_at,
        )
        for t in tickets
    ]


@router.get("/{ticket_id}", response_model=TicketDetail)
def detalle_ticket(
    ticket_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TicketDetail:
    return _detail(db, _ticket_accesible(db, user, ticket_id))


@router.post("/{ticket_id}/reply", response_model=TicketDetail)
def responder_ticket(
    ticket_id: int,
    payload: ResponderInput,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TicketDetail:
    ticket = _ticket_accesible(db, user, ticket_id)
    try:
        service.responder(db, user, ticket, payload.mensaje)
    except service.SupportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _detail(db, ticket)


@router.post("/{ticket_id}/close", response_model=TicketDetail)
def cerrar_ticket(
    ticket_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TicketDetail:
    ticket = _ticket_accesible(db, user, ticket_id)
    service.cerrar(db, ticket)
    return _detail(db, ticket)
