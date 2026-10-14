"""DTOs del módulo Correos."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.modules.correos.infrastructure.models import MailboxConnectionModel


class AuthorizeRequest(BaseModel):
    """Inicia la vinculación de un buzón por OAuth."""
    provider: Literal["gmail", "outlook"]


class AuthorizeResponse(BaseModel):
    authorization_url: str


class CallbackRequest(BaseModel):
    """Completa la vinculación con el código y el state devueltos por el proveedor."""
    code: str = Field(min_length=1, max_length=4096)
    state: str = Field(min_length=16, max_length=4096)


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
