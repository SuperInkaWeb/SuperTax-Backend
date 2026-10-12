"""Acceso a datos del módulo DataFilter: datasets por empresa (aislamiento tenant)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.datafilter.infrastructure.models import DatasetModel


class SqlDatasetRepository:
    """Datasets de una empresa. Toda consulta filtra por `company_id` para que
    un tenant nunca vea los datasets de otro."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_by_company(self, company_id: int) -> list[DatasetModel]:
        stmt = (
            select(DatasetModel)
            .where(DatasetModel.company_id == company_id)
            .order_by(DatasetModel.created_at.desc())
        )
        return list(self.db.scalars(stmt))

    def get(self, dataset_id: int, company_id: int) -> DatasetModel | None:
        stmt = select(DatasetModel).where(
            DatasetModel.id == dataset_id,
            DatasetModel.company_id == company_id,
        )
        return self.db.scalar(stmt)
