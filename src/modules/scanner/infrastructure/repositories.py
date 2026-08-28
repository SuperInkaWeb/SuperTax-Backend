"""Repositorio SQLAlchemy del módulo Scanner (schema `scanner`)."""
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.modules.scanner.infrastructure.models import (
    DocumentoModel,
    ScannerJobModel,
    ScannerJobStatus,
)
from src.platform.database.base import utcnow


class SqlScannerJobRepository:
    """Cola de extracción: jobs que consume el worker (scanner_worker)."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create(
        self,
        company_id: int,
        user_id: int,
        nombre_archivo: str,
        storage_path: str,
        tipo_forzado: str | None = None,
    ) -> ScannerJobModel:
        job = ScannerJobModel(
            company_id=company_id,
            created_by_id=user_id,
            nombre_archivo=nombre_archivo,
            storage_path=storage_path,
            tipo_forzado=tipo_forzado,
        )
        self._db.add(job)
        self._db.commit()
        self._db.refresh(job)
        return job

    def get(self, job_id: int, company_id: int) -> ScannerJobModel | None:
        return self._db.scalar(
            select(ScannerJobModel).where(
                ScannerJobModel.id == job_id,
                ScannerJobModel.company_id == company_id,
            )
        )

    def claim(self, job_id: int) -> bool:
        """Reclama el job de forma atómica (`en_cola` → `procesando`). True si este
        proceso lo ganó; False si otro ya lo tomó o no está en cola. Evita el
        doble-procesado (web on-demand y/o un worker de respaldo)."""
        result = self._db.execute(
            update(ScannerJobModel)
            .where(
                ScannerJobModel.id == job_id,
                ScannerJobModel.status == ScannerJobStatus.en_cola,
            )
            .values(status=ScannerJobStatus.procesando)
        )
        self._db.commit()
        return result.rowcount == 1

    def ids_por_estado(self, status: ScannerJobStatus) -> list[int]:
        return list(
            self._db.scalars(
                select(ScannerJobModel.id).where(ScannerJobModel.status == status)
            ).all()
        )

    def marcar_estado_masivo(
        self, de_estado: ScannerJobStatus, a_estado: ScannerJobStatus, mensaje: str | None = None
    ) -> int:
        """Cambia el estado de todos los jobs en `de_estado`. Si `mensaje`, lo pone
        como `error_message`. Devuelve cuántos cambió."""
        valores: dict = {"status": a_estado}
        if mensaje is not None:
            valores["error_message"] = mensaje[:500]
        result = self._db.execute(
            update(ScannerJobModel)
            .where(ScannerJobModel.status == de_estado)
            .values(**valores)
        )
        self._db.commit()
        return result.rowcount

    def mark_completado(self, job_id: int, documento_id: int) -> None:
        job = self._db.get(ScannerJobModel, job_id)
        if job is not None:
            job.status = ScannerJobStatus.completado
            job.documento_id = documento_id
            job.completed_at = utcnow()
            self._db.commit()

    def mark_error(self, job_id: int, mensaje: str) -> None:
        job = self._db.get(ScannerJobModel, job_id)
        if job is not None:
            job.status = ScannerJobStatus.error
            job.error_message = mensaje[:500]
            job.completed_at = utcnow()
            self._db.commit()


class SqlDocumentoRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_by_company(
        self, company_id: int, tipo: str | None, limit: int, offset: int
    ) -> list[DocumentoModel]:
        query = select(DocumentoModel).where(DocumentoModel.company_id == company_id)
        if tipo and tipo != "todos":
            query = query.where(DocumentoModel.tipo_documento == tipo)
        query = query.order_by(DocumentoModel.created_at.desc()).limit(limit).offset(offset)
        return list(self._db.scalars(query).all())

    def get(self, doc_id: int, company_id: int) -> DocumentoModel | None:
        return self._db.scalar(
            select(DocumentoModel).where(
                DocumentoModel.id == doc_id,
                DocumentoModel.company_id == company_id,
            )
        )

    def update_campos(self, doc_id: int, company_id: int, campos: dict) -> DocumentoModel | None:
        doc = self.get(doc_id, company_id)
        if doc is None:
            return None
        merged = dict(doc.campos or {})
        merged.update(campos)
        doc.campos = merged
        self._db.commit()
        self._db.refresh(doc)
        return doc

    def create(
        self,
        company_id: int,
        user_id: int,
        tipo_documento: str,
        tipo_etiqueta: str | None,
        confianza: float | None,
        nombre_archivo: str,
        storage_path: str | None,
        campos: dict,
    ) -> DocumentoModel:
        doc = DocumentoModel(
            company_id=company_id,
            created_by_id=user_id,
            tipo_documento=tipo_documento,
            tipo_etiqueta=tipo_etiqueta,
            confianza=confianza,
            nombre_archivo=nombre_archivo,
            storage_path=storage_path,
            campos=campos,
        )
        self._db.add(doc)
        self._db.commit()
        self._db.refresh(doc)
        return doc
