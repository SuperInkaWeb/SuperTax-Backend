"""DTOs del módulo Correos."""
from datetime import datetime

from pydantic import BaseModel

from src.modules.correos.infrastructure.models import MailboxConnectionModel


class MailboxSummary(BaseModel):
    """Vista de una conexión de buzón (nunca expone los tokens)."""
    id: int
    provider: str
    account_email: str
    status: str
    created_at: datetime

    @classmethod
    def from_model(cls, m: MailboxConnectionModel) -> "MailboxSummary":
        return cls(
            id=m.id,
            provider=m.provider,
            account_email=m.account_email,
            status=m.status,
            created_at=m.created_at,
        )
