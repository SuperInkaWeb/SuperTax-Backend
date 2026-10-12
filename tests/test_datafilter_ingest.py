"""
Ingesta de DataFilter (Fase 2): conversión a Parquet con DuckDB.

No usa BD (eso corre en CI con Postgres). Prueba la función pura `convertir`
sobre storage local: CSV → Parquet, esquema detectado y limpieza del crudo.
"""
import os

import pytest

from src.platform.config.settings import settings


@pytest.fixture
def storage_local(tmp_path, monkeypatch):
    """Apunta el storage de la plataforma a un directorio temporal (modo local)."""
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path))
    return tmp_path


def _escribir_fuente(company_id: int, dataset_id: int, extension: str, contenido: str) -> str:
    from src.modules.datafilter.infrastructure import storage_access

    key = storage_access.source_key(company_id, dataset_id, extension)
    ruta = storage_access.locator(key)
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    return ruta


def test_convertir_csv_a_parquet(storage_local):
    from src.modules.datafilter.application.ingest import convertir
    from src.modules.datafilter.infrastructure import storage_access

    contenido = "serie,igv,fecha\nB001,69.05,2026-08-01\nB002,20.12,2026-08-02\n"
    ruta = _escribir_fuente(1, 5, ".csv", contenido)
    res = convertir({"company_id": 1, "dataset_id": 5, "extension": ".csv", "sheet": None})

    assert res["row_count"] == 2
    assert [c["name"] for c in res["columns"]] == ["serie", "igv", "fecha"]
    assert any(c["type"] == "DOUBLE" for c in res["columns"])          # igv tipado como número
    assert os.path.exists(storage_access.locator(storage_access.parquet_key(1, 5)))
    assert not os.path.exists(ruta)                                     # el CSV crudo se borra


def test_convertir_archivo_solo_cabecera_lanza(storage_local):
    from src.modules.datafilter.application.ingest import convertir

    _escribir_fuente(1, 6, ".csv", "serie,igv\n")  # sin filas de datos
    with pytest.raises(ValueError):
        convertir({"company_id": 1, "dataset_id": 6, "extension": ".csv", "sheet": None})
