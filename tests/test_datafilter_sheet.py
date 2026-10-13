"""Validaciones de 'cambiar hoja' (Fase 6): sin BD, con un repo de stub."""
import pytest

from src.modules.datafilter.infrastructure.models import DatasetModel, DatasetOrigin
from src.modules.datafilter.infrastructure.repositories import SqlDatasetRepository


class _RepoStub(SqlDatasetRepository):
    """Repo que no toca BD: `get` devuelve un dataset fijo."""

    def __init__(self, ds: DatasetModel | None) -> None:  # noqa: D401 - stub de prueba
        self._ds = ds

    def get(self, dataset_id: int, company_id: int) -> DatasetModel | None:
        return self._ds


def _excel(sheets: list[str]) -> DatasetModel:
    return DatasetModel(
        company_id=1, created_by_id=1, original_filename="libro.xlsx", extension=".xlsx",
        origin=DatasetOrigin.uploaded.value, source_path="datafilter/1/1/source.xlsx",
        sheets=sheets, columns=[],
    )


def test_cambiar_hoja_inexistente_rechazada():
    repo = _RepoStub(_excel(["Hoja1", "Hoja2"]))
    with pytest.raises(ValueError):
        repo.request_sheet_change(1, 1, "NoExiste")


def test_cambiar_hoja_en_derivado_rechazada():
    derivado = DatasetModel(
        company_id=1, created_by_id=1, original_filename="pivote", extension=".parquet",
        origin=DatasetOrigin.pivot.value, source_path=None, sheets=[], columns=[],
    )
    with pytest.raises(ValueError):
        _RepoStub(derivado).request_sheet_change(1, 1, "Hoja1")


def test_cambiar_hoja_dataset_inexistente_devuelve_none():
    assert _RepoStub(None).request_sheet_change(99, 1, "Hoja1") is None
