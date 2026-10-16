"""Acceso a datos del módulo Correos (aislamiento por empresa en toda consulta)."""
from datetime import date, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from src.modules.correos.infrastructure.models import (
    AttachmentModel,
    AuditLogModel,
    EmailMessageModel,
    ExtractedRecordModel,
    MailboxConnectionModel,
    MailboxStatus,
    ProcessingErrorModel,
    ReviewStatus,
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

    def get(self, attachment_id: int, company_id: int) -> AttachmentModel | None:
        return self.db.scalar(
            select(AttachmentModel).where(
                AttachmentModel.id == attachment_id, AttachmentModel.company_id == company_id
            )
        )

    def pendientes_de_extraccion(
        self, company_id: int, job_id: int
    ) -> list[AttachmentModel]:
        """Adjuntos de un escaneo que aún no produjeron registro. Idempotente: si el
        escaneo se reejecuta, los ya extraídos no se vuelven a procesar."""
        ya_extraido = (
            exists()
            .where(ExtractedRecordModel.attachment_id == AttachmentModel.id)
            .where(ExtractedRecordModel.company_id == company_id)
        )
        stmt = (
            select(AttachmentModel)
            .join(EmailMessageModel, AttachmentModel.message_id == EmailMessageModel.id)
            .where(
                EmailMessageModel.job_id == job_id,
                AttachmentModel.company_id == company_id,
                ~ya_extraido,
            )
            .order_by(AttachmentModel.id)
        )
        return list(self.db.scalars(stmt))

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


class SqlRecordRepository:
    """Registros tributarios extraídos de los adjuntos (aislados por empresa)."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def existe_para_adjunto(self, company_id: int, attachment_id: int) -> bool:
        return self.db.scalar(
            select(ExtractedRecordModel.id).where(
                ExtractedRecordModel.company_id == company_id,
                ExtractedRecordModel.attachment_id == attachment_id,
            )
        ) is not None

    def create(self, valores: dict) -> ExtractedRecordModel:
        """Crea un registro a partir de los valores de columna ya convertidos a su
        tipo (Decimal/date) por el servicio de extracción."""
        registro = ExtractedRecordModel(**valores)
        self.db.add(registro)
        self.db.commit()
        self.db.refresh(registro)
        return registro

    def get(self, record_id: int, company_id: int) -> ExtractedRecordModel | None:
        return self.db.scalar(
            select(ExtractedRecordModel).where(
                ExtractedRecordModel.id == record_id,
                ExtractedRecordModel.company_id == company_id,
            )
        )

    def listar(
        self, company_id: int, *, limit: int, offset: int,
        job_id: int | None = None, ruc: str | None = None, ruc_inquilino: str | None = None,
        periodo: str | None = None, periodo_desde: str | None = None,
        periodo_hasta: str | None = None, fecha_desde: date | None = None,
        fecha_hasta: date | None = None, solo_aprobados: bool = False,
        solo_pendientes: bool = False, ids: list[int] | None = None,
    ) -> tuple[list[ExtractedRecordModel], int]:
        """Listado filtrado y paginado + total que cumple el filtro (para la UI)."""
        filtros = self._filtros(
            company_id, job_id=job_id, ruc=ruc, ruc_inquilino=ruc_inquilino, periodo=periodo,
            periodo_desde=periodo_desde, periodo_hasta=periodo_hasta, fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta, solo_aprobados=solo_aprobados, solo_pendientes=solo_pendientes,
            ids=ids,
        )
        total = self.db.scalar(
            select(func.count()).select_from(ExtractedRecordModel).where(*filtros)
        ) or 0
        items = list(self.db.scalars(
            select(ExtractedRecordModel).where(*filtros)
            .order_by(ExtractedRecordModel.created_at.desc(), ExtractedRecordModel.id.desc())
            .limit(limit).offset(offset)
        ))
        return items, int(total)

    def contar_pendientes(self, company_id: int) -> int:
        total = self.db.scalar(
            select(func.count()).select_from(ExtractedRecordModel).where(
                ExtractedRecordModel.company_id == company_id,
                ExtractedRecordModel.estado_de_revision == ReviewStatus.pending.value,
            )
        )
        return int(total or 0)

    def delete(self, record_id: int, company_id: int) -> bool:
        registro = self.get(record_id, company_id)
        if registro is None:
            return False
        self.db.delete(registro)
        self.db.commit()
        return True

    def commit(self) -> None:
        """Persiste los cambios hechos sobre un registro obtenido con `get`."""
        self.db.commit()

    def origenes(self, company_id: int, attachment_ids: list[int]) -> dict[int, dict]:
        """Procedencia (correo + nombre de adjunto) por `attachment_id`, en una sola
        consulta por lote (evita N+1). La extracción no conoce estas tablas; se unen
        aquí, en el borde, para que la UI sepa de qué correo salió cada registro."""
        if not attachment_ids:
            return {}
        filas = self.db.execute(
            select(
                AttachmentModel.id, AttachmentModel.original_name,
                EmailMessageModel.sender, EmailMessageModel.subject,
                EmailMessageModel.received_at,
            )
            .join(EmailMessageModel, AttachmentModel.message_id == EmailMessageModel.id)
            .where(
                AttachmentModel.company_id == company_id,
                AttachmentModel.id.in_(attachment_ids),
            )
        ).all()
        return {
            fila.id: {
                "nombre_adjunto": fila.original_name, "remitente": fila.sender,
                "asunto": fila.subject, "recibido_en": fila.received_at,
            }
            for fila in filas
        }

    @staticmethod
    def _filtros(
        company_id: int, *, job_id, ruc, ruc_inquilino, periodo, periodo_desde,
        periodo_hasta, fecha_desde, fecha_hasta, solo_aprobados, solo_pendientes,
        ids=None,
    ) -> list:
        """Construye las condiciones WHERE compartidas por el listado y su conteo."""
        m = ExtractedRecordModel
        condiciones: list = [m.company_id == company_id]
        if ids:  # "exportar solo estos": selección explícita del usuario
            condiciones.append(m.id.in_(ids))
        if job_id is not None:
            condiciones.append(m.job_id == job_id)
        if ruc:
            condiciones.append(m.ruc_contribuyente == ruc)
        if ruc_inquilino:
            condiciones.append(m.ruc_inquilino == ruc_inquilino)
        if periodo:
            condiciones.append(m.periodo == periodo)
        if periodo_desde:  # YYYYMM: el orden lexicográfico es cronológico
            condiciones.append(m.periodo >= periodo_desde)
        if periodo_hasta:
            condiciones.append(m.periodo <= periodo_hasta)
        if fecha_desde:
            condiciones.append(m.fecha_de_pago >= fecha_desde)
        if fecha_hasta:
            condiciones.append(m.fecha_de_pago <= fecha_hasta)
        if solo_aprobados:
            condiciones.append(
                m.estado_de_revision.in_(
                    (ReviewStatus.approved.value, ReviewStatus.not_required.value)
                )
            )
        if solo_pendientes:
            condiciones.append(m.estado_de_revision == ReviewStatus.pending.value)
        return condiciones


class SqlAuditRepository:
    """Bitácora de solo-inserción (append-only) de acciones sensibles."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def add(
        self, *, company_id: int, actor_id: int | None, action: str, resource_type: str = "",
        resource_id: str | None = None, actor_ip: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        self.db.add(AuditLogModel(
            company_id=company_id, actor_id=actor_id, actor_ip=actor_ip, action=action,
            resource_type=resource_type, resource_id=resource_id,
            audit_metadata=metadata or {},
        ))
        self.db.commit()

    def listar(
        self, company_id: int, *, limit: int, offset: int
    ) -> tuple[list[AuditLogModel], int]:
        condicion = AuditLogModel.company_id == company_id
        total = self.db.scalar(
            select(func.count()).select_from(AuditLogModel).where(condicion)
        ) or 0
        items = list(self.db.scalars(
            select(AuditLogModel).where(condicion)
            .order_by(AuditLogModel.occurred_at.desc(), AuditLogModel.id.desc())
            .limit(limit).offset(offset)
        ))
        return items, int(total)
