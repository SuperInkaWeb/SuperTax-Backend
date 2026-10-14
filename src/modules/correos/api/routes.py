"""
Endpoints del módulo Correos (montados bajo /api/correos).

Fase 1 (andamiaje): listar las conexiones de buzón de la empresa. El vínculo OAuth,
el escaneo, la extracción, la revisión y la exportación llegan en fases posteriores.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.modules.correos.api.schemas import MailboxSummary
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
