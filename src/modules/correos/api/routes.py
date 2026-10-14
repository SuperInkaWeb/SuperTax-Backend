"""
Endpoints del módulo Correos (montados bajo /api/correos).

Fase 1-2: conexiones de buzón — listar, vincular por OAuth (Gmail/Outlook) y
desconectar. El escaneo, la extracción, la revisión y la exportación llegan después.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.modules.correos.api.schemas import (
    AuthorizeRequest,
    AuthorizeResponse,
    CallbackRequest,
    MailboxSummary,
)
from src.modules.correos.application import mailbox_oauth
from src.modules.correos.infrastructure.repositories import SqlMailboxRepository
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import get_db
from src.platform.tenancy.current_tenant import ActiveContext

router = APIRouter(tags=["correos"], dependencies=[Depends(require_module("correos"))])


@router.get("/mailboxes", response_model=list[MailboxSummary])
def listar_buzones(
    ctx: ActiveContext = Depends(require_permission("correos.mailbox.read")),
    db: Session = Depends(get_db),
) -> list[MailboxSummary]:
    """Conexiones de buzón de la empresa activa, de la más reciente a la más antigua."""
    buzones = SqlMailboxRepository(db).list_by_company(ctx.company.id)
    return [MailboxSummary.from_model(b) for b in buzones]


@router.post("/mailboxes/authorize", response_model=AuthorizeResponse)
def autorizar_buzon(
    body: AuthorizeRequest,
    ctx: ActiveContext = Depends(require_permission("correos.mailbox.create")),
) -> AuthorizeResponse:
    """Devuelve la URL de consentimiento OAuth. El frontend redirige al proveedor."""
    try:
        url = mailbox_oauth.autorizar(ctx.company.id, ctx.user.id, body.provider)
    except mailbox_oauth.OAuthError as exc:
        raise HTTPException(exc.status_code, detail=exc.message) from exc
    return AuthorizeResponse(authorization_url=url)


@router.post(
    "/mailboxes/callback", response_model=MailboxSummary, status_code=status.HTTP_201_CREATED
)
def callback_buzon(
    body: CallbackRequest,
    ctx: ActiveContext = Depends(require_permission("correos.mailbox.create")),
    db: Session = Depends(get_db),
) -> MailboxSummary:
    """Completa la vinculación: canjea el código por tokens y guarda la conexión."""
    try:
        conn = mailbox_oauth.completar(db, ctx.company.id, ctx.user.id, body.code, body.state)
    except mailbox_oauth.OAuthError as exc:
        raise HTTPException(exc.status_code, detail=exc.message) from exc
    return MailboxSummary.from_model(conn)


@router.delete("/mailboxes/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def desconectar_buzon(
    connection_id: int,
    ctx: ActiveContext = Depends(require_permission("correos.mailbox.delete")),
    db: Session = Depends(get_db),
) -> None:
    """Desconecta (borra) una conexión de buzón de la empresa activa."""
    if not SqlMailboxRepository(db).delete(connection_id, ctx.company.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conexión no encontrada")
