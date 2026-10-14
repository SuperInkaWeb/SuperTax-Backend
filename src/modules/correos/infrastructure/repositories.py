"""Acceso a datos del módulo Correos (aislamiento por empresa en toda consulta)."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.correos.infrastructure.models import (
    AttachmentModel,
    EmailMessageModel,
    MailboxConnectionModel,
    MailboxStatus,
    ProcessingErrorModel,
    ScanJobModel,
    ScanPhase,
    ScanStatus,
)
from src.platform.database.base import utcnow

_INTERRUMPIDO = "El escaneo se interrumpió (reinicio del servidor). Vuelve a lanzarlo."


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


class SqlScanRepository:
    """Trabajos de escaneo de una empresa (estado/progreso/contadores en BD)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self, *, company_id: int, requested_by_id: int, connection_id: int, params: dict
    ) -> ScanJobModel:
        job = ScanJobModel(
            company_id=company_id,
            requested_by_id=requested_by_id,
            connection_id=connection_id,
            status=ScanStatus.queued.value,
            phase=ScanPhase.waiting.value,
            params=params,
            counters={},
        )
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job

    def list_by_company(self, company_id: int) -> list[ScanJobModel]:
        stmt = (
            select(ScanJobModel)
            .where(ScanJobModel.company_id == company_id)
            .order_by(ScanJobModel.created_at.desc())
        )
        return list(self.db.scalars(stmt))

    def get(self, job_id: int, company_id: int) -> ScanJobModel | None:
        return self.db.scalar(
            select(ScanJobModel).where(
                ScanJobModel.id == job_id, ScanJobModel.company_id == company_id
            )
        )

    def claim(self, job_id: int) -> tuple[int, int, dict] | None:
        """Reclama el job: `queued` → `running`. Devuelve (company_id, connection_id,
        params) o None si ya lo tomó otro o fue cancelado antes de arrancar."""
        job = self.db.get(ScanJobModel, job_id)
        if job is None or job.status != ScanStatus.queued.value:
            return None
        job.status = ScanStatus.running.value
        job.phase = ScanPhase.listing.value
        job.started_at = utcnow()
        self.db.commit()
        return job.company_id, job.connection_id, dict(job.params or {})

    def update_progress(self, job_id: int, *, phase: str, progress: int, counters: dict) -> None:
        job = self.db.get(ScanJobModel, job_id)
        if job is not None:
            job.phase = phase
            job.progress_pct = progress
            job.counters = counters
            self.db.commit()

    def cancel_requested(self, job_id: int) -> bool:
        job = self.db.get(ScanJobModel, job_id)
        return job is not None and job.status == ScanStatus.cancelled.value

    def request_cancel(self, job_id: int, company_id: int) -> bool:
        job = self.get(job_id, company_id)
        if job is None or job.status not in (ScanStatus.queued.value, ScanStatus.running.value):
            return False
        job.status = ScanStatus.cancelled.value
        job.finished_at = utcnow()
        self.db.commit()
        return True

    def set_completed(self, job_id: int, counters: dict) -> None:
        job = self.db.get(ScanJobModel, job_id)
        if job is None or job.status == ScanStatus.cancelled.value:
            return  # un cancel durante el escaneo gana
        job.status = ScanStatus.completed.value
        job.phase = ScanPhase.done.value
        job.progress_pct = 100
        job.counters = counters
        job.finished_at = utcnow()
        self.db.commit()

    def set_failed(self, job_id: int, code: str, message: str) -> None:
        job = self.db.get(ScanJobModel, job_id)
        if job is None or job.status == ScanStatus.cancelled.value:
            return
        job.status = ScanStatus.failed.value
        job.error_code = code
        job.error_message = message[:500]
        job.finished_at = utcnow()
        self.db.commit()

    def ids_pendientes(self) -> list[int]:
        return list(self.db.scalars(
            select(ScanJobModel.id).where(ScanJobModel.status == ScanStatus.queued.value)
        ))

    def marcar_interrumpidos(self) -> int:
        rows = list(self.db.scalars(
            select(ScanJobModel).where(ScanJobModel.status == ScanStatus.running.value)
        ))
        for job in rows:
            job.status = ScanStatus.failed.value
            job.error_code = "interrupted"
            job.error_message = _INTERRUMPIDO
            job.finished_at = utcnow()
        if rows:
            self.db.commit()
        return len(rows)


class SqlMessageRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert(
        self, *, company_id: int, job_id: int, provider: str, provider_id: str,
        sender: str, subject: str, received_at,
    ) -> EmailMessageModel:
        """Idempotente por (company, provider, provider_id): reusa el correo si ya existe."""
        existing = self.db.scalar(
            select(EmailMessageModel).where(
                EmailMessageModel.company_id == company_id,
                EmailMessageModel.provider == provider,
                EmailMessageModel.provider_id == provider_id,
            )
        )
        if existing is not None:
            return existing
        msg = EmailMessageModel(
            company_id=company_id, job_id=job_id, provider=provider, provider_id=provider_id,
            sender=sender, subject=subject, received_at=received_at,
        )
        self.db.add(msg)
        self.db.commit()
        self.db.refresh(msg)
        return msg


class SqlAttachmentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def existe(self, company_id: int, sha256: str) -> bool:
        return self.db.scalar(
            select(AttachmentModel.id).where(
                AttachmentModel.company_id == company_id, AttachmentModel.sha256 == sha256
            )
        ) is not None

    def create(
        self, *, company_id: int, message_id: int, original_name: str, storage_key: str,
        mime_type: str, size_bytes: int, sha256: str,
    ) -> AttachmentModel:
        att = AttachmentModel(
            company_id=company_id, message_id=message_id, original_name=original_name,
            storage_key=storage_key, mime_type=mime_type, size_bytes=size_bytes, sha256=sha256,
        )
        self.db.add(att)
        self.db.commit()
        self.db.refresh(att)
        return att


class SqlErrorRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def add(
        self, *, company_id: int, job_id: int, stage: str, code: str, message: str,
        context: dict | None = None, retryable: bool = False,
    ) -> None:
        self.db.add(ProcessingErrorModel(
            company_id=company_id, job_id=job_id, stage=stage, code=code,
            message=message[:1000], context=context or {}, retryable=retryable,
        ))
        self.db.commit()

    def list_by_job(self, job_id: int, company_id: int) -> list[ProcessingErrorModel]:
        return list(self.db.scalars(
            select(ProcessingErrorModel)
            .where(
                ProcessingErrorModel.company_id == company_id,
                ProcessingErrorModel.job_id == job_id,
            )
            .order_by(ProcessingErrorModel.created_at.desc())
        ))
