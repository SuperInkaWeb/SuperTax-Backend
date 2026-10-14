"""DTOs del módulo Correos."""
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from src.modules.correos.domain.extraction import UMBRAL_DE_CONFIANZA
from src.modules.correos.infrastructure.models import (
    ExtractedRecordModel,
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


class RecordOrigin(BaseModel):
    """Procedencia de un registro: el correo y el adjunto del que salió."""
    nombre_adjunto: str = ""
    remitente: str = ""
    asunto: str = ""
    recibido_en: datetime | None = None


class RecordSummary(BaseModel):
    """Un registro tributario extraído, con calidad y procedencia (para la revisión)."""
    id: int
    job_id: int
    attachment_id: int
    ruc_contribuyente: str | None
    nombre_contribuyente: str
    ruc_inquilino: str | None
    nombre_inquilino: str
    tipo_doc_inquilino: str
    tipo_de_bien: str
    periodo: str | None
    fecha_de_pago: date | None
    numero_de_operacion: str | None
    monto_alquiler: str | None
    tributo_resultante: str | None
    importe_pagado: str | None
    intereses_moratorios: str | None
    moneda: str
    confianza_por_campo: dict
    campos_dudosos: list[str]
    completitud: str
    estado_de_revision: str
    estrategia_usada: str | None
    created_at: datetime
    origen: RecordOrigin

    @classmethod
    def from_model(cls, m: ExtractedRecordModel, origen: dict | None = None) -> "RecordSummary":
        confianzas = dict(m.confianza_por_campo or {})
        dudosos = sorted(n for n, c in confianzas.items() if c < UMBRAL_DE_CONFIANZA)
        return cls(
            id=m.id, job_id=m.job_id, attachment_id=m.attachment_id,
            ruc_contribuyente=m.ruc_contribuyente, nombre_contribuyente=m.nombre_contribuyente,
            ruc_inquilino=m.ruc_inquilino, nombre_inquilino=m.nombre_inquilino,
            tipo_doc_inquilino=m.tipo_doc_inquilino, tipo_de_bien=m.tipo_de_bien,
            periodo=m.periodo, fecha_de_pago=m.fecha_de_pago,
            numero_de_operacion=m.numero_de_operacion,
            monto_alquiler=_importe(m.monto_alquiler),
            tributo_resultante=_importe(m.tributo_resultante),
            importe_pagado=_importe(m.importe_pagado),
            intereses_moratorios=_importe(m.intereses_moratorios),
            moneda=m.moneda, confianza_por_campo=confianzas, campos_dudosos=dudosos,
            completitud=m.completitud, estado_de_revision=m.estado_de_revision,
            estrategia_usada=m.estrategia_usada, created_at=m.created_at,
            origen=RecordOrigin(**(origen or {})),
        )


class RecordListResponse(BaseModel):
    """Página de registros con el total que cumple el filtro (para la paginación)."""
    items: list[RecordSummary]
    total: int
    limit: int
    offset: int


class CorreccionRequest(BaseModel):
    """Correcciones campo→valor (texto). Se validan con los objetos de valor y aprueban."""
    correcciones: dict[str, str] = Field(min_length=1)


def _importe(valor) -> str | None:  # noqa: ANN001 - Decimal | None del modelo
    """Serializa un importe como texto con dos decimales (evita el ruido del float)."""
    return f"{valor:.2f}" if valor is not None else None
