"""Repositorios SQLAlchemy del módulo SUNAT (schema `sunat`)."""
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.modules.sunat.domain.entities import JobResult
from src.modules.sunat.infrastructure.models import (
    DriveTokenModel,
    JobResultModel,
    SunatCredentialsModel,
    SunatJobLogModel,
    SunatJobModel,
    SunatJobStatus,
)
from src.platform.database.base import utcnow

_ESTADOS_TERMINALES = (
    SunatJobStatus.completado,
    SunatJobStatus.error,
    SunatJobStatus.cancelado,
)


class SqlSunatJobRepository:
    """Cola de descargas SUNAT + logs (canal SSE sobre Postgres)."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def create(
        self, company_id: int, user_id: int, job_id: str, config_enc: str, excel_path: str
    ) -> SunatJobModel:
        job = SunatJobModel(
            job_id=job_id,
            company_id=company_id,
            created_by_id=user_id,
            config_enc=config_enc,
            excel_path=excel_path,
        )
        self._db.add(job)
        self._db.commit()
        return job

    def get(self, job_id: str) -> SunatJobModel | None:
        """Sin filtro de empresa: uso interno del worker."""
        return self._db.scalar(
            select(SunatJobModel).where(SunatJobModel.job_id == job_id)
        )

    def get_scoped(self, job_id: str, company_id: int) -> SunatJobModel | None:
        return self._db.scalar(
            select(SunatJobModel).where(
                SunatJobModel.job_id == job_id,
                SunatJobModel.company_id == company_id,
            )
        )

    def list_by_company(
        self, company_id: int, limit: int, offset: int
    ) -> list[SunatJobModel]:
        return list(
            self._db.scalars(
                select(SunatJobModel)
                .where(SunatJobModel.company_id == company_id)
                .order_by(SunatJobModel.created_at.desc())
                .limit(limit)
                .offset(offset)
            ).all()
        )

    def claim(self, job_id: str) -> bool:
        """Reclama el job de forma atómica (`en_cola` → `procesando`). Devuelve
        True si este proceso ganó el job; False si otro ya lo tomó o no está en
        cola. El guard por estado evita el doble-procesado (web on-demand y/o un
        worker de respaldo)."""
        result = self._db.execute(
            update(SunatJobModel)
            .where(
                SunatJobModel.job_id == job_id,
                SunatJobModel.status == SunatJobStatus.en_cola,
            )
            .values(status=SunatJobStatus.procesando)
        )
        self._db.commit()
        return result.rowcount == 1

    def ids_por_estado(self, status: SunatJobStatus) -> list[str]:
        return list(
            self._db.scalars(
                select(SunatJobModel.job_id).where(SunatJobModel.status == status)
            ).all()
        )

    def marcar_estado_masivo(
        self, de_estado: SunatJobStatus, a_estado: SunatJobStatus
    ) -> int:
        """Cambia el estado de todos los jobs en `de_estado`. Devuelve cuántos."""
        result = self._db.execute(
            update(SunatJobModel)
            .where(SunatJobModel.status == de_estado)
            .values(status=a_estado)
        )
        self._db.commit()
        return result.rowcount

    def set_status(self, job_id: str, status: SunatJobStatus) -> None:
        job = self.get(job_id)
        if job is not None:
            job.status = status
            if status in _ESTADOS_TERMINALES:
                job.completed_at = utcnow()
            self._db.commit()

    def request_cancel(self, job_id: str, company_id: int) -> bool:
        job = self.get_scoped(job_id, company_id)
        if job is None:
            return False
        job.cancel_requested = True
        self._db.commit()
        return True

    def logs_after(self, job_id: str, after_id: int) -> list[SunatJobLogModel]:
        return list(
            self._db.scalars(
                select(SunatJobLogModel)
                .where(
                    SunatJobLogModel.job_id == job_id,
                    SunatJobLogModel.id > after_id,
                )
                .order_by(SunatJobLogModel.id)
            ).all()
        )


class SqlSunatCredentialsRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, company_id: int) -> SunatCredentialsModel | None:
        return self._db.scalar(
            select(SunatCredentialsModel).where(
                SunatCredentialsModel.company_id == company_id
            )
        )

    def upsert(
        self,
        company_id: int,
        updated_by_id: int,
        ruc: str,
        usuario_enc: str,
        clave_enc: str,
    ) -> None:
        creds = self.get(company_id)
        if creds is None:
            creds = SunatCredentialsModel(company_id=company_id)
            self._db.add(creds)
        creds.ruc = ruc
        creds.usuario_enc = usuario_enc
        creds.clave_enc = clave_enc
        creds.updated_by_id = updated_by_id
        self._db.commit()


class SqlJobResultRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_by_company(
        self, company_id: int, limit: int, offset: int
    ) -> list[JobResult]:
        rows = self._db.scalars(
            select(JobResultModel)
            .where(JobResultModel.company_id == company_id)
            .order_by(JobResultModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [
            JobResult(id=r.id, job_id=r.job_id, created_at=r.created_at, resultados=r.resultados)
            for r in rows
        ]

    def get_by_job_id(self, job_id: str, company_id: int) -> JobResultModel | None:
        return self._db.scalar(
            select(JobResultModel).where(
                JobResultModel.job_id == job_id,
                JobResultModel.company_id == company_id,
            )
        )

    def job_ids_con_resultado(self, job_ids: list[str]) -> set[str]:
        """Subconjunto de job_ids que ya tienen resultados guardados."""
        if not job_ids:
            return set()
        rows = self._db.scalars(
            select(JobResultModel.job_id).where(JobResultModel.job_id.in_(job_ids))
        ).all()
        return set(rows)


class SqlDriveTokenRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, user_id: int) -> DriveTokenModel | None:
        return self._db.scalar(
            select(DriveTokenModel).where(DriveTokenModel.user_id == user_id)
        )

    def upsert(self, user_id: int, access_enc: str, refresh_enc: str) -> None:
        token = self.get(user_id)
        if token is None:
            token = DriveTokenModel(user_id=user_id)
            self._db.add(token)
        token.access_token_enc = access_enc
        token.refresh_token_enc = refresh_enc
        self._db.commit()

    def delete(self, user_id: int) -> None:
        token = self.get(user_id)
        if token is not None:
            self._db.delete(token)
            self._db.commit()
