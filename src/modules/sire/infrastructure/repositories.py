"""Repositorios SQLAlchemy del módulo SIRE (schema `sire`)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.sire.domain.entities import (
    JobStatus,
    ReconciliationJob,
    TipoLibro,
)
from src.modules.sire.infrastructure.models import (
    ReconciliationJobModel,
    ReconciliationResultModel,
    SireCredentialsModel,
)


class SqlReconciliationRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_by_company(
        self, company_id: int, limit: int, offset: int
    ) -> list[ReconciliationJob]:
        rows = self._db.scalars(
            select(ReconciliationJobModel)
            .where(ReconciliationJobModel.company_id == company_id)
            .order_by(ReconciliationJobModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [self._to_entity(row) for row in rows]

    def get(self, job_id: int, company_id: int) -> ReconciliationJob | None:
        row = self._db.scalar(
            select(ReconciliationJobModel).where(
                ReconciliationJobModel.id == job_id,
                ReconciliationJobModel.company_id == company_id,
            )
        )
        return self._to_entity(row) if row else None

    def create(
        self,
        company_id: int,
        user_id: int,
        periodo: str,
        tipo_libro: TipoLibro,
        filename: str | None,
    ) -> ReconciliationJob:
        row = ReconciliationJobModel(
            company_id=company_id,
            created_by_id=user_id,
            periodo=periodo,
            tipo_libro=tipo_libro,
            status=JobStatus.en_cola,
            empresa_filename=filename,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return self._to_entity(row)

    def set_file_path(self, job_id: int, storage_path: str) -> None:
        row = self._db.get(ReconciliationJobModel, job_id)
        if row is not None:
            row.empresa_file_path = storage_path
            self._db.commit()

    def _to_entity(self, row: ReconciliationJobModel) -> ReconciliationJob:
        result = self._db.scalar(
            select(ReconciliationResultModel).where(
                ReconciliationResultModel.job_id == row.id
            )
        )
        return ReconciliationJob(
            id=row.id,
            company_id=row.company_id,
            periodo=row.periodo,
            tipo_libro=row.tipo_libro,
            status=row.status,
            created_at=row.created_at,
            empresa_filename=row.empresa_filename,
            completed_at=row.completed_at,
            error_message=row.error_message,
            igv_diferencia_total=(
                float(result.igv_diferencia_total) if result else None
            ),
            tiene_alertas_rojas=result.tiene_alertas_rojas if result else None,
        )


class SqlCredentialsRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, company_id: int) -> SireCredentialsModel | None:
        return self._db.scalar(
            select(SireCredentialsModel).where(
                SireCredentialsModel.company_id == company_id
            )
        )

    def upsert(
        self,
        company_id: int,
        updated_by_id: int,
        usuario_sol: str,
        clave_sol_enc: str,
        client_id: str,
        client_secret_enc: str,
    ) -> None:
        creds = self.get(company_id)
        if creds is None:
            creds = SireCredentialsModel(company_id=company_id)
            self._db.add(creds)
        creds.usuario_sol = usuario_sol
        creds.clave_sol_enc = clave_sol_enc
        creds.client_id = client_id
        creds.client_secret_enc = client_secret_enc
        creds.updated_by_id = updated_by_id
        self._db.commit()
