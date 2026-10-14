"""Acceso a datos del módulo Correos (aislamiento por empresa en toda consulta)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.correos.infrastructure.models import MailboxConnectionModel


class SqlMailboxRepository:
    """Conexiones de buzón de una empresa. Toda consulta filtra por `company_id`
    para que un tenant nunca vea las conexiones de otro."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_by_company(self, company_id: int) -> list[MailboxConnectionModel]:
        stmt = (
            select(MailboxConnectionModel)
            .where(MailboxConnectionModel.company_id == company_id)
            .order_by(MailboxConnectionModel.created_at.desc())
        )
        return list(self.db.scalars(stmt))
