"""
Filtrado, vista previa y descarga de un dataset.

Valida las columnas contra el esquema del dataset (whitelist del query-builder),
ejecuta la consulta en DuckDB sobre el Parquet (local o R2) y, para descargar,
escribe el resultado a un temporal que la ruta transmite. Todo filtra por empresa.
"""
import tempfile
from pathlib import Path

from sqlalchemy.orm import Session

from src.modules.datafilter.domain.filtering import FilterGroup, SortSpec
from src.modules.datafilter.infrastructure import (
    duckdb_engine,
    heavy,
    query_builder,
    storage_access,
)
from src.modules.datafilter.infrastructure.models import DatasetModel, DatasetStatus
from src.modules.datafilter.infrastructure.repositories import SqlDatasetRepository
from src.platform.config.settings import settings

_DISTINCT_LIMIT = 500
_XLSX_MAX_DATA_ROWS = 1_048_575  # tope duro de filas por hoja en XLSX (incl. cabecera)
_MEDIA_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "txt": "text/plain; charset=utf-8",
}


class DatasetNotFound(Exception):
    """El dataset no existe (o no es de la empresa activa)."""


class DatasetNotReady(Exception):
    """El dataset aún no terminó de procesarse."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


class DownloadTooLarge(Exception):
    """El resultado filtrado excede el máximo de filas descargables."""


def requerir_listo(db: Session, dataset_id: int, company_id: int) -> DatasetModel:
    """Devuelve el dataset de la empresa o lanza si no existe / aún no está listo.
    Compartido por el filtrado y el análisis (columnas calculadas, dedupe, etc.)."""
    ds = SqlDatasetRepository(db).get(dataset_id, company_id)
    if ds is None:
        raise DatasetNotFound()
    if ds.status != DatasetStatus.ready.value:
        raise DatasetNotReady(ds.status)
    return ds


def locator_parquet(company_id: int, dataset_id: int) -> str:
    return storage_access.locator(storage_access.parquet_key(company_id, dataset_id))


def columnas_validas(ds: DatasetModel) -> set[str]:
    return {c["name"] for c in (ds.columns or [])}


def preparar_destino_export(
    ds: DatasetModel, formato: str, matched: int, sufijo: str
) -> tuple[str, str, str]:
    """Valida el tope de filas, crea un temporal y arma el nombre de descarga.

    Devuelve (ruta_temporal, media_type, nombre); el llamador escribe el archivo en
    la ruta. Compartido por el filtrado y las descargas de análisis (DRY)."""
    _guard_download(matched, formato)
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=f".{formato}")
    handle.close()
    nombre = f"{_sanitize(ds.original_filename)}_{sufijo}.{formato}"
    return handle.name, _MEDIA_TYPES[formato], nombre


def preview(
    db: Session, dataset_id: int, company_id: int, *,
    filtro: FilterGroup | None, seleccion: list[str], orden: list[SortSpec],
    limit: int, offset: int, search: str | None,
) -> tuple[list[str], list[dict], int]:
    """Aplica filtros y devuelve (columnas, filas, total de coincidencias)."""
    ds = requerir_listo(db, dataset_id, company_id)
    valid = columnas_validas(ds)
    where_sql, params = query_builder.build_where(filtro, valid)
    where_sql, params = query_builder.apply_search(where_sql, params, search, valid)
    select_sql = query_builder.build_select(seleccion, valid)
    order_sql = query_builder.build_order_by(orden, valid)
    parquet = locator_parquet(company_id, dataset_id)

    columns, rows = duckdb_engine.preview(
        parquet, select_sql, where_sql, order_sql, limit, offset, params
    )
    # Sin filtro, el total es el conteo guardado: evita un escaneo extra del Parquet.
    total = (
        duckdb_engine.count_matches(parquet, where_sql, params)
        if where_sql else (ds.row_count or 0)
    )
    return columns, rows, total


def distinct_values(
    db: Session, dataset_id: int, company_id: int, column: str, search: str | None
) -> tuple[list[str], bool]:
    """Valores únicos de una columna para el filtro tipo Excel. Devuelve (valores, truncado)."""
    ds = requerir_listo(db, dataset_id, company_id)
    valid = columnas_validas(ds)
    column_sql = query_builder.quote_column(column, valid)  # valida contra whitelist
    values, truncated = duckdb_engine.distinct_values(
        locator_parquet(company_id, dataset_id), column_sql, search, _DISTINCT_LIMIT
    )
    return [str(v) for v in values], truncated


def _guard_download(matched: int, formato: str) -> None:
    tope = settings.DATAFILTER_MAX_DOWNLOAD_ROWS
    if matched > tope:
        raise DownloadTooLarge(
            f"El resultado tiene {matched:,} filas y supera el máximo de {tope:,}."
        )
    if formato == "xlsx" and matched > _XLSX_MAX_DATA_ROWS:
        raise DownloadTooLarge(
            f"XLSX admite máximo {_XLSX_MAX_DATA_ROWS:,} filas; el resultado tiene "
            f"{matched:,}. Usa CSV."
        )


def _sanitize(filename: str) -> str:
    """Nombre base seguro para la descarga (sin ruta ni caracteres peligrosos)."""
    stem = Path(filename).stem
    safe = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in stem)
    return safe or "export"


def build_export(
    db: Session, dataset_id: int, company_id: int, *,
    filtro: FilterGroup | None, seleccion: list[str], orden: list[SortSpec],
    search: str | None, formato: str, delimiter_char: str | None,
) -> tuple[str, str, str]:
    """Genera el archivo filtrado en un temporal. Devuelve (ruta, media_type, nombre)."""
    ds = requerir_listo(db, dataset_id, company_id)
    valid = columnas_validas(ds)
    where_sql, params = query_builder.build_where(filtro, valid)
    where_sql, params = query_builder.apply_search(where_sql, params, search, valid)
    select_sql = query_builder.build_select(seleccion, valid)
    order_sql = query_builder.build_order_by(orden, valid)
    parquet = locator_parquet(company_id, dataset_id)

    # Operación pesada (recorre todo el archivo): toma cupo del presupuesto global.
    with heavy.gate():
        matched = duckdb_engine.count_matches(parquet, where_sql, params)
        ruta, media_type, nombre = preparar_destino_export(ds, formato, matched, "filtrado")
        duckdb_engine.export_to_file(
            parquet, select_sql, where_sql, order_sql, params, ruta, formato, delimiter_char
        )
    return ruta, media_type, nombre


def eliminar(db: Session, dataset_id: int, company_id: int) -> bool:
    """Borra el dataset: sus archivos en storage (crudo + Parquet) y su fila."""
    repo = SqlDatasetRepository(db)
    ds = repo.get(dataset_id, company_id)
    if ds is None:
        return False
    for key in (ds.source_path, storage_access.parquet_key(company_id, dataset_id)):
        if key:
            try:
                storage_access.delete(key)
            except Exception:  # limpieza best-effort: no bloquear el borrado del registro
                pass
    repo.delete(dataset_id, company_id)
    return True
