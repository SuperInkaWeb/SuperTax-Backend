"""Acceso a datos del módulo Correos (aislamiento por empresa en toda consulta)."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.correos.infrastructure.models import (
    MailboxConnectionModel,
    MailboxStatus,
)
from src.platform.database.base import utcnow


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

    def get(self, connection_id: int, company_id: int) -> MailboxConnectionModel | None:
        stmt = select(MailboxConnectionModel).where(
            MailboxConnectionModel.id == connection_id,
            MailboxConnectionModel.company_id == company_id,
        )
        return self.db.scalar(stmt)

    def upsert_connection(
        self, *, company_id: int, created_by_id: int, provider: str, account_email: str,
        access_token_enc: str, refresh_token_enc: str | None, scopes: list[str],
        expires_in_seconds: int,
    ) -> MailboxConnectionModel:
        """Crea o actualiza la conexión del (empresa, usuario, proveedor). Reconectar
        actualiza la fila, no acumula credenciales viejas (unicidad en la tabla)."""
        stmt = select(MailboxConnectionModel).where(
            MailboxConnectionModel.company_id == company_id,
            MailboxConnectionModel.created_by_id == created_by_id,
            MailboxConnectionModel.provider == provider,
        )
        conn = self.db.scalar(stmt)
        if conn is None:
            conn = MailboxConnectionModel(
                company_id=company_id, created_by_id=created_by_id, provider=provider,
            )
            self.db.add(conn)
        conn.account_email = account_email
        conn.access_token = access_token_enc
        conn.refresh_token = refresh_token_enc
        conn.scopes = scopes
        conn.expires_at = utcnow() + timedelta(seconds=expires_in_seconds)
        conn.status = MailboxStatus.active.value
        conn.verified_at = utcnow()
        self.db.commit()
        self.db.refresh(conn)
        return conn

    def delete(self, connection_id: int, company_id: int) -> bool:
        conn = self.get(connection_id, company_id)
        if conn is None:
            return False
        self.db.delete(conn)
        self.db.commit()
        return True
