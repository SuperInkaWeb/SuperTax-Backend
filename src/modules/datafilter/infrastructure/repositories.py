"""Acceso a datos del módulo DataFilter: datasets por empresa (aislamiento tenant)."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.datafilter.infrastructure.models import (
    DatasetModel,
    DatasetOrigin,
    DatasetStatus,
)
from src.modules.datafilter.infrastructure.storage_access import parquet_key

_INTERRUMPIDO = (
    "El procesamiento se interrumpió (reinicio del servidor). Vuelve a subir el archivo."
)


class SqlDatasetRepository:
    """Datasets de una empresa. Toda consulta filtra por `company_id` para que un
    tenant nunca vea ni toque los datasets de otro."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # ── Lectura ──────────────────────────────────────────────────────────

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

    # ── Alta y subida ────────────────────────────────────────────────────

    def create(
        self, *, company_id: int, created_by_id: int, original_filename: str, extension: str
    ) -> DatasetModel:
        ds = DatasetModel(
            company_id=company_id,
            created_by_id=created_by_id,
            original_filename=original_filename,
            extension=extension,
            status=DatasetStatus.pending.value,
            origin=DatasetOrigin.uploaded.value,
            columns=[],
            sheets=[],
        )
        self.db.add(ds)
        self.db.commit()
        self.db.refresh(ds)
        return ds

    def set_uploaded(self, dataset_id: int, size_bytes: int, source_path: str) -> None:
        ds = self.db.get(DatasetModel, dataset_id)
        if ds is not None:
            ds.size_bytes = size_bytes
            ds.source_path = source_path
            self.db.commit()

    # ── Ingesta (estado) ─────────────────────────────────────────────────

    def claim(self, dataset_id: int) -> tuple[int, str, str | None] | None:
        """Reclama un dataset para procesarlo: lo pasa de `pending` a `processing`.

        Devuelve (company_id, extension, active_sheet) o None si ya lo tomó otro
        (o no está en `pending`), para no procesarlo dos veces."""
        ds = self.db.get(DatasetModel, dataset_id)
        if ds is None or ds.status != DatasetStatus.pending.value:
            return None
        ds.status = DatasetStatus.processing.value
        self.db.commit()
        return ds.company_id, ds.extension, ds.active_sheet

    def set_ready(self, dataset_id: int, result: dict) -> None:
        ds = self.db.get(DatasetModel, dataset_id)
        if ds is None:
            return
        ds.status = DatasetStatus.ready.value
        ds.columns = result["columns"]
        ds.row_count = result["row_count"]
        if result.get("sheets") is not None:
            ds.sheets = result["sheets"]
            ds.active_sheet = result.get("active_sheet")
        ds.parquet_path = parquet_key(ds.company_id, ds.id)
        ds.error = None
        self.db.commit()

    def set_failed(self, dataset_id: int, error: str) -> None:
        ds = self.db.get(DatasetModel, dataset_id)
        if ds is not None:
            ds.status = DatasetStatus.failed.value
            ds.error = error[:500]
            self.db.commit()

    # ── Recuperación al arrancar ─────────────────────────────────────────

    def marcar_interrumpidos(self) -> int:
        """Los que quedaron en `processing` (redeploy a mitad) pasan a `failed`."""
        rows = list(
            self.db.scalars(
                select(DatasetModel).where(DatasetModel.status == DatasetStatus.processing.value)
            )
        )
        for ds in rows:
            ds.status = DatasetStatus.failed.value
            ds.error = _INTERRUMPIDO
        if rows:
            self.db.commit()
        return len(rows)

    def ids_pendientes(self) -> list[int]:
        return list(
            self.db.scalars(
                select(DatasetModel.id).where(DatasetModel.status == DatasetStatus.pending.value)
            )
        )
