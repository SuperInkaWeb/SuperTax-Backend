"""
Modelos de persistencia del módulo Correos (schema `correos`).

Automatiza la extracción de datos tributarios SUNAT desde adjuntos de correo:
se vincula un buzón (Gmail/Outlook) por OAuth, se lanza un escaneo que descarga
adjuntos y un pipeline OCR/IA extrae los campos, que luego se revisan y exportan.

Adaptado a las convenciones de la plataforma (frente al standalone):
  - PK enteras; multi-tenant por `company_id` (FK a `core.companies`), no RLS.
  - Tokens OAuth cifrados con el Fernet de la plataforma (texto), no BYTEA+DEK.
  - Estados/fases como texto (los valores de los enums de abajo), no ENUM de PG.
  - JSON en vez de JSONB para portabilidad.
Identity (empresas/usuarios) se reutiliza del núcleo; aquí solo vive el dominio.
"""
import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.platform.database.base import Base, utcnow


class MailboxProvider(str, enum.Enum):
    gmail = "gmail"
    outlook = "outlook"


class MailboxStatus(str, enum.Enum):
    active = "active"        # conexión viva, token válido
    revoked = "revoked"      # el usuario desconectó o revocó el acceso
    error = "error"          # el token dejó de funcionar


class ScanStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class ScanPhase(str, enum.Enum):
    waiting = "waiting"
    listing = "listing"          # listando correos del buzón
    downloading = "downloading"  # descargando adjuntos
    extracting = "extracting"    # OCR/IA sobre los adjuntos
    done = "done"


class ReviewStatus(str, enum.Enum):
    not_required = "not_required"  # confianza alta: no necesita revisión
    pending = "pending"            # en cola de revisión humana
    approved = "approved"
    rejected = "rejected"


class Completeness(str, enum.Enum):
    empty = "empty"
    partial = "partial"
    complete = "complete"


class ExportStatus(str, enum.Enum):
    queued = "queued"
    running = "running"
    completed = "completed"
    failed = "failed"


class MailboxConnectionModel(Base):
    """Conexión OAuth a un buzón (Gmail/Outlook). Un usuario tiene como mucho una
    conexión viva por proveedor dentro de su empresa (reconectar actualiza la fila)."""

    __tablename__ = "mailbox_connections"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "created_by_id", "provider", name="uq_correos_mailbox_company_user_prov"
        ),
        {"schema": "correos"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("core.users.id"))
    provider: Mapped[str] = mapped_column(String(20))
    account_email: Mapped[str] = mapped_column(String(320), default="")
    # Tokens OAuth cifrados con Fernet (platform.security): nunca en texto plano.
    access_token: Mapped[str] = mapped_column(Text)
    refresh_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    scopes: Mapped[list] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default=MailboxStatus.active.value, index=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ScanJobModel(Base):
    """Un escaneo: descarga adjuntos de un buzón y los pasa por el pipeline. Su estado
    y progreso viven en la BD (no en memoria), así sobrevive a reinicios y escala."""

    __tablename__ = "scan_jobs"
    __table_args__ = (
        UniqueConstraint("company_id", "idempotency_key", name="uq_correos_scan_idempotency"),
        {"schema": "correos"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    requested_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL"), nullable=True
    )
    connection_id: Mapped[int] = mapped_column(
        ForeignKey("correos.mailbox_connections.id", ondelete="CASCADE")
    )
    status: Mapped[str] = mapped_column(String(20), default=ScanStatus.queued.value, index=True)
    phase: Mapped[str] = mapped_column(String(20), default=ScanPhase.waiting.value)
    progress_pct: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    counters: Mapped[dict] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class EmailMessageModel(Base):
    """Un correo escaneado. La unicidad (company, provider, provider_id) hace idempotente
    la ingesta: el mismo correo nunca se procesa dos veces para una empresa."""

    __tablename__ = "email_messages"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "provider", "provider_id", name="uq_correos_msg_company_prov_id"
        ),
        {"schema": "correos"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("correos.scan_jobs.id", ondelete="CASCADE"), index=True
    )
    provider: Mapped[str] = mapped_column(String(20))
    provider_id: Mapped[str] = mapped_column(String(255))
    sender: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AttachmentModel(Base):
    """Un adjunto descargado a R2. La unicidad por sha256 deduplica el mismo archivo."""

    __tablename__ = "attachments"
    __table_args__ = (
        UniqueConstraint("company_id", "sha256", name="uq_correos_attach_company_sha"),
        {"schema": "correos"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    message_id: Mapped[int] = mapped_column(
        ForeignKey("correos.email_messages.id", ondelete="CASCADE"), index=True
    )
    original_name: Mapped[str] = mapped_column(String(255), default="")
    storage_key: Mapped[str] = mapped_column(String(512))
    mime_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str] = mapped_column(String(64))
    antivirus_status: Mapped[str] = mapped_column(String(20), default="skipped")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ExtractedRecordModel(Base):
    """Datos tributarios extraídos de un adjunto. Columnas tipadas (para filtrar/ordenar)
    + copia cruda en JSON (para diagnosticar). Un adjunto produce como mucho un registro."""

    __tablename__ = "extracted_records"
    __table_args__ = (
        UniqueConstraint("company_id", "attachment_id", name="uq_correos_record_company_attach"),
        {"schema": "correos"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    attachment_id: Mapped[int] = mapped_column(
        ForeignKey("correos.attachments.id", ondelete="CASCADE")
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("correos.scan_jobs.id", ondelete="CASCADE"), index=True
    )
    profile: Mapped[str] = mapped_column(String(64))

    # ── Datos tributarios ──
    ruc_contribuyente: Mapped[str | None] = mapped_column(String(11), nullable=True, index=True)
    nombre_contribuyente: Mapped[str] = mapped_column(String(120), default="")
    ruc_inquilino: Mapped[str | None] = mapped_column(String(11), nullable=True)
    nombre_inquilino: Mapped[str] = mapped_column(String(120), default="")
    tipo_doc_inquilino: Mapped[str] = mapped_column(String(40), default="")
    tipo_de_bien: Mapped[str] = mapped_column(String(40), default="")
    periodo: Mapped[str | None] = mapped_column(String(6), nullable=True, index=True)  # YYYYMM
    fecha_de_pago: Mapped[date | None] = mapped_column(Date, nullable=True)
    numero_de_operacion: Mapped[str | None] = mapped_column(String(40), nullable=True)
    monto_alquiler: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    tributo_resultante: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    importe_pagado: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    intereses_moratorios: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    moneda: Mapped[str] = mapped_column(String(3), default="PEN")

    # ── Calidad y trazabilidad ──
    campos_crudos: Mapped[dict] = mapped_column(JSON, default=dict)
    confianza_por_campo: Mapped[dict] = mapped_column(JSON, default=dict)
    completitud: Mapped[str] = mapped_column(String(20), default=Completeness.empty.value)
    estado_de_revision: Mapped[str] = mapped_column(
        String(20), default=ReviewStatus.not_required.value, index=True
    )
    revisado_por_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL"), nullable=True
    )
    revisado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    estrategia_usada: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duracion_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProcessingErrorModel(Base):
    """Un fallo de una etapa del escaneo (listar/descargar/extraer), para la vista de errores."""

    __tablename__ = "processing_errors"
    __table_args__ = ({"schema": "correos"},)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    job_id: Mapped[int] = mapped_column(
        ForeignKey("correos.scan_jobs.id", ondelete="CASCADE"), index=True
    )
    stage: Mapped[str] = mapped_column(String(20))
    code: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(Text, default="")
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    retryable: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class ReportExportModel(Base):
    """Una exportación (Excel) del reporte consolidado; el archivo final vive en R2."""

    __tablename__ = "report_exports"
    __table_args__ = ({"schema": "correos"},)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    requested_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL"), nullable=True
    )
    format: Mapped[str] = mapped_column(String(10), default="xlsx")
    status: Mapped[str] = mapped_column(String(20), default=ExportStatus.queued.value)
    filters: Mapped[dict] = mapped_column(JSON, default=dict)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class AuditLogModel(Base):
    """Bitácora de solo-inserción de acciones sensibles (aprobar/rechazar/borrar/exportar)."""

    __tablename__ = "audit_log"
    __table_args__ = ({"schema": "correos"},)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.users.id", ondelete="SET NULL"), nullable=True
    )
    actor_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64))
    resource_type: Mapped[str] = mapped_column(String(64), default="")
    resource_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    audit_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
