"""DTOs del módulo Correos."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.modules.correos.infrastructure.models import (
    MailboxConnectionModel,
    ProcessingErrorModel,
    ScanJobModel,
)


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


class ScanRequest(BaseModel):
    """Lanza un escaneo del buzón acotado por fecha y cantidad."""
    connection_id: int
    desde: date | None = None
    hasta: date | None = None
    limite_de_mensajes: int = Field(default=100, ge=1, le=50_000)
    carpeta: str = Field(default="INBOX", max_length=120, pattern=r"^[\w\-/ ]+$")


class ScanSummary(BaseModel):
    """Estado y contadores de un escaneo (para seguir el progreso)."""
    id: int
    status: str
    phase: str
    progress_pct: int
    counters: dict
    error_code: str | None
    error_message: str | None
    queued_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_model(cls, m: ScanJobModel) -> "ScanSummary":
        return cls(
            id=m.id, status=m.status, phase=m.phase, progress_pct=m.progress_pct,
            counters=dict(m.counters or {}), error_code=m.error_code,
            error_message=m.error_message, queued_at=m.queued_at,
            started_at=m.started_at, finished_at=m.finished_at,
        )


class ErrorSummary(BaseModel):
    """Un error de procesamiento de un escaneo (vista de errores)."""
    stage: str
    code: str
    message: str
    created_at: datetime

    @classmethod
    def from_model(cls, m: ProcessingErrorModel) -> "ErrorSummary":
        return cls(stage=m.stage, code=m.code, message=m.message, created_at=m.created_at)
