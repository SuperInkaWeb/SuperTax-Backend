"""Implementación SQLAlchemy del repositorio de conciliaciones (schema `sire`)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.sire.domain.entities import ReconciliationJob
from src.modules.sire.infrastructure.models import (
    ReconciliationJobModel,
    ReconciliationResultModel,
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
            completed_at=row.completed_at,
            error_message=row.error_message,
            igv_diferencia_total=(
                float(result.igv_diferencia_total) if result else None
            ),
            tiene_alertas_rojas=result.tiene_alertas_rojas if result else None,
        )
