"""
Análisis de un dataset: columnas calculadas, buscar-y-reemplazar, eliminar
duplicados y estadísticas por columna.

Cada operación ofrece "ver" (una página, sin persistir) y "descargar" (archivo
completo). Las tres transformaciones ofrecen además "guardar como dataset nuevo":
materializan el resultado a un Parquet y registran un dataset DERIVADO
(origin=computed|replaced|deduped) reutilizando la máquina de estados
PROCESSING->READY y el sondeo del frontend (igual que la ingesta normal).

Reutiliza los helpers compartidos de `query` (requerir_listo, locator_parquet,
columnas_validas, preparar_destino_export) para no duplicar lógica. El trabajo
pesado de DuckDB se materializa en un subproceso efímero (ver `analysis_dispatch`);
este módulo solo planifica el SQL y da el target picklable del hijo.
"""
from sqlalchemy.orm import Session

from src.modules.datafilter.application import query
from src.modules.datafilter.domain.analysis import PivotSpec, ReplacementRule
from src.modules.datafilter.domain.expressions import ComputedColumn
from src.modules.datafilter.domain.filtering import FilterGroup, SortDirection, SortSpec
from src.modules.datafilter.infrastructure import (
    dedupe_builder,
    duckdb_engine,
    expression_builder,
    heavy,
    pivot_builder,
    query_builder,
    replace_builder,
)
from src.modules.datafilter.infrastructure.models import DatasetModel, DatasetOrigin
from src.modules.datafilter.infrastructure.repositories import SqlDatasetRepository

# Subcadenas de tipo DuckDB que identifican columnas numéricas (INT cubre TINYINT..HUGEINT).
_NUMERIC_TYPE_HINTS = ("INT", "DECIMAL", "DOUBLE", "FLOAT", "REAL", "NUMERIC")
_STATS_TOP_LIMIT = 10  # cuántos valores frecuentes se muestran en el perfil de una columna


def _orden(order_column: str | None, direction: SortDirection, columnas: set[str]) -> str:
    """ORDER BY validado por una columna del RESULTADO (whitelist), o '' si no aplica."""
    if not order_column:
        return ""
    return query_builder.build_order_by(
        [SortSpec(column=order_column, direction=direction)], columnas
    )


def _numeric_columns(ds: DatasetModel) -> set[str]:
    """Nombres de columnas numéricas del dataset (para validar SUMA/PROMEDIO/estadísticas)."""
    return {
        col["name"]
        for col in (ds.columns or [])
        if any(hint in col["type"].upper() for hint in _NUMERIC_TYPE_HINTS)
    }


# ── Columnas calculadas ──────────────────────────────────────────────────

def run_compute(
    db: Session, dataset_id: int, company_id: int, *,
    filtro: FilterGroup | None, columnas: list[ComputedColumn],
    orden_col: str | None, orden_dir: SortDirection, limit: int, offset: int,
) -> tuple[list[str], list[dict], int]:
    """Aplica las columnas calculadas y devuelve (columnas, filas, total). Incluye las
    originales y las nuevas; por ambas se puede ordenar."""
    ds = query.requerir_listo(db, dataset_id, company_id)
    valid = query.columnas_validas(ds)
    where_sql, where_params = query_builder.build_where(filtro, valid)
    select_sql, compute_params = expression_builder.build_compute_select(columnas, valid)
    result_cols = valid | {c.name.strip() for c in columnas}
    order_sql = _orden(orden_col, orden_dir, result_cols)
    parquet = query.locator_parquet(company_id, dataset_id)
    with heavy.gate():  # pesada (recorre todo el archivo): cupo del presupuesto global
        total = duckdb_engine.count_matches(parquet, where_sql, where_params)
        cols, rows = duckdb_engine.preview(
            parquet, select_sql, where_sql, order_sql, limit, offset, compute_params + where_params
        )
    return cols, rows, total


def _destino(
    ds, company_id: int, dataset_id: int, formato: str, matched: int, sufijo: str, a_r2: bool
) -> tuple[str, str, str, str]:
    """Resuelve el destino de una descarga: en R2 (clave + locator de escritura) o en
    un temporal local. Devuelve (salida, destino_escritura, media_type, nombre), donde
    `salida` es lo que se retorna (clave en R2 o ruta local) y `destino_escritura` es
    adonde escribe el export."""
    if a_r2:
        return query.destino_export_r2(ds, company_id, dataset_id, formato, matched, sufijo)
    ruta, media, nombre = query.preparar_destino_export(ds, formato, matched, sufijo)
    return ruta, ruta, media, nombre


def export_compute(
    db: Session, dataset_id: int, company_id: int, *,
    filtro: FilterGroup | None, columnas: list[ComputedColumn],
    formato: str, delimiter_char: str | None, a_r2: bool = False,
) -> tuple[str, str, str]:
    """Descarga TODAS las filas con las columnas calculadas. `a_r2=True` escribe el
    resultado en R2 y devuelve (clave, media, nombre); si no, (ruta_temporal, media, nombre)."""
    ds = query.requerir_listo(db, dataset_id, company_id)
    valid = query.columnas_validas(ds)
    where_sql, where_params = query_builder.build_where(filtro, valid)
    select_sql, compute_params = expression_builder.build_compute_select(columnas, valid)
    parquet = query.locator_parquet(company_id, dataset_id)
    with heavy.gate():
        matched = duckdb_engine.count_matches(parquet, where_sql, where_params)
        salida, destino, media, nombre = _destino(
            ds, company_id, dataset_id, formato, matched, "columnas", a_r2
        )
        duckdb_engine.export_to_file(
            parquet, select_sql, where_sql, "", compute_params + where_params,
            destino, formato, delimiter_char,
        )
    return salida, media, nombre


# ── Buscar y reemplazar por columna ──────────────────────────────────────

def run_replace(
    db: Session, dataset_id: int, company_id: int, *,
    filtro: FilterGroup | None, replacements: list[ReplacementRule],
    orden_col: str | None, orden_dir: SortDirection, limit: int, offset: int,
) -> tuple[list[str], list[dict], int]:
    """Aplica las correcciones y devuelve (columnas, filas, total). Conserva las columnas."""
    ds = query.requerir_listo(db, dataset_id, company_id)
    valid = query.columnas_validas(ds)
    where_sql, where_params = query_builder.build_where(filtro, valid)
    column_order = [c["name"] for c in (ds.columns or [])]
    select_sql, params = replace_builder.build_replace_select(replacements, column_order, valid)
    order_sql = _orden(orden_col, orden_dir, valid)
    parquet = query.locator_parquet(company_id, dataset_id)
    with heavy.gate():
        total = duckdb_engine.count_matches(parquet, where_sql, where_params)
        cols, rows = duckdb_engine.preview(
            parquet, select_sql, where_sql, order_sql, limit, offset, params + where_params
        )
    return cols, rows, total


def export_replace(
    db: Session, dataset_id: int, company_id: int, *,
    filtro: FilterGroup | None, replacements: list[ReplacementRule],
    formato: str, delimiter_char: str | None, a_r2: bool = False,
) -> tuple[str, str, str]:
    """Descarga TODAS las filas ya corregidas. `a_r2=True` → (clave_R2, media, nombre)."""
    ds = query.requerir_listo(db, dataset_id, company_id)
    valid = query.columnas_validas(ds)
    where_sql, where_params = query_builder.build_where(filtro, valid)
    column_order = [c["name"] for c in (ds.columns or [])]
    select_sql, params = replace_builder.build_replace_select(replacements, column_order, valid)
    parquet = query.locator_parquet(company_id, dataset_id)
    with heavy.gate():
        matched = duckdb_engine.count_matches(parquet, where_sql, where_params)
        salida, destino, media, nombre = _destino(
            ds, company_id, dataset_id, formato, matched, "corregido", a_r2
        )
        duckdb_engine.export_to_file(
            parquet, select_sql, where_sql, "", params + where_params,
            destino, formato, delimiter_char,
        )
    return salida, media, nombre


# ── Eliminar duplicados ──────────────────────────────────────────────────

def run_dedupe(
    db: Session, dataset_id: int, company_id: int, *,
    filtro: FilterGroup | None, key_columns: list[str],
    orden_col: str | None, orden_dir: SortDirection, limit: int, offset: int,
) -> tuple[list[str], list[dict], int, int]:
    """Quita duplicados. Devuelve (columnas, filas, total_sin_duplicados, total_original)."""
    ds = query.requerir_listo(db, dataset_id, company_id)
    valid = query.columnas_validas(ds)
    where_sql, where_params = query_builder.build_where(filtro, valid)
    select_sql, qualify_sql = dedupe_builder.build_dedupe(key_columns, valid)
    order_sql = _orden(orden_col, orden_dir, valid)
    parquet = query.locator_parquet(company_id, dataset_id)
    with heavy.gate():
        original = duckdb_engine.count_matches(parquet, where_sql, where_params)
        total = duckdb_engine.dedupe_count(
            parquet, select_sql, where_sql, qualify_sql, where_params
        )
        cols, rows = duckdb_engine.dedupe_preview(
            parquet, select_sql, where_sql, qualify_sql, where_params, limit, offset, order_sql
        )
    return cols, rows, total, original


def export_dedupe(
    db: Session, dataset_id: int, company_id: int, *,
    filtro: FilterGroup | None, key_columns: list[str],
    formato: str, delimiter_char: str | None, a_r2: bool = False,
) -> tuple[str, str, str]:
    """Descarga TODAS las filas sin duplicados. `a_r2=True` → (clave_R2, media, nombre)."""
    ds = query.requerir_listo(db, dataset_id, company_id)
    valid = query.columnas_validas(ds)
    where_sql, where_params = query_builder.build_where(filtro, valid)
    select_sql, qualify_sql = dedupe_builder.build_dedupe(key_columns, valid)
    parquet = query.locator_parquet(company_id, dataset_id)
    with heavy.gate():
        matched = duckdb_engine.dedupe_count(
            parquet, select_sql, where_sql, qualify_sql, where_params
        )
        salida, destino, media, nombre = _destino(
            ds, company_id, dataset_id, formato, matched, "sin_duplicados", a_r2
        )
        duckdb_engine.dedupe_export_to_file(
            parquet, select_sql, where_sql, qualify_sql, where_params,
            destino, formato, delimiter_char,
        )
    return salida, media, nombre


# ── Estadísticas por columna ─────────────────────────────────────────────

def run_stats(
    db: Session, dataset_id: int, company_id: int, *,
    filtro: FilterGroup | None, column: str,
) -> dict:
    """Perfil descriptivo de una columna: conteos, mín/máx, suma/promedio y top valores."""
    ds = query.requerir_listo(db, dataset_id, company_id)
    valid = query.columnas_validas(ds)
    column_sql = query_builder.quote_column(column, valid)  # valida contra whitelist
    numeric = column in _numeric_columns(ds)
    where_sql, where_params = query_builder.build_where(filtro, valid)
    parquet = query.locator_parquet(company_id, dataset_id)

    with heavy.gate():
        row = duckdb_engine.column_stats(parquet, column_sql, where_sql, where_params, numeric)
        top = duckdb_engine.column_top_values(
            parquet, column_sql, where_sql, where_params, _STATS_TOP_LIMIT
        )
    total, non_null, distinct = int(row[0]), int(row[1]), int(row[2])
    return {
        "column": column,
        "is_numeric": numeric,
        "total": total,
        "non_null": non_null,
        "nulls": total - non_null,
        "distinct": distinct,
        "minimum": _scalar(row[3]),
        "maximum": _scalar(row[4]),
        "total_sum": row[5] if numeric else None,
        "average": row[6] if numeric else None,
        "top_values": [{"value": value, "count": count} for value, count in top],
    }


def _scalar(value: object) -> str | float | int | None:
    """Normaliza un mín/máx: números tal cual, el resto como texto."""
    if value is None or isinstance(value, (int, float)):
        return value
    return str(value)


# ── Tablas dinámicas (pivote) ────────────────────────────────────────────

def run_pivot(
    db: Session, dataset_id: int, company_id: int, *,
    spec: PivotSpec, limit: int, offset: int,
) -> tuple[list[str], list[dict], int, dict]:
    """Ejecuta el pivote. Devuelve (columnas, filas, total_agrupado, fila_total_general).

    La fila de Total general (métricas sobre todas las filas) es solo para fijarla al pie
    de la vista previa; no se persiste ni se descarga."""
    _, valid, numeric, where_sql, where_params, pivot_values = _pivot_context(
        db, dataset_id, company_id, spec
    )
    select_sql, group_cols_sql, order_sql, params = pivot_builder.build_pivot(
        spec, valid, numeric, pivot_values
    )
    parquet = query.locator_parquet(company_id, dataset_id)
    totals_select, totals_params = pivot_builder.build_pivot_totals(
        spec, valid, numeric, pivot_values
    )
    with heavy.gate():
        total = duckdb_engine.pivot_count(parquet, group_cols_sql, where_sql, where_params)
        cols, rows = duckdb_engine.pivot_preview(
            parquet, select_sql, group_cols_sql, where_sql, params + where_params,
            limit, offset, order_sql,
        )
        totals = duckdb_engine.pivot_totals(
            parquet, totals_select, where_sql, totals_params + where_params
        )
    return cols, rows, total, totals


def export_pivot(
    db: Session, dataset_id: int, company_id: int, *,
    spec: PivotSpec, formato: str, delimiter_char: str | None, a_r2: bool = False,
) -> tuple[str, str, str]:
    """Descarga el reporte pivote COMPLETO. `a_r2=True` → (clave_R2, media, nombre)."""
    ds, valid, numeric, where_sql, where_params, pivot_values = _pivot_context(
        db, dataset_id, company_id, spec
    )
    select_sql, group_cols_sql, order_sql, params = pivot_builder.build_pivot(
        spec, valid, numeric, pivot_values
    )
    parquet = query.locator_parquet(company_id, dataset_id)
    with heavy.gate():
        matched = duckdb_engine.pivot_count(parquet, group_cols_sql, where_sql, where_params)
        salida, destino, media, nombre = _destino(
            ds, company_id, dataset_id, formato, matched, "reporte", a_r2
        )
        duckdb_engine.pivot_export_to_file(
            parquet, select_sql, group_cols_sql, where_sql, params + where_params,
            destino, formato, delimiter_char, order_sql,
        )
    return salida, media, nombre


def _pivot_context(
    db: Session, dataset_id: int, company_id: int, spec: PivotSpec,
) -> tuple[DatasetModel, set[str], set[str], str, list, list | None]:
    """Piezas comunes del pivote (DRY entre ver/descargar/guardar): devuelve
    (ds, columnas_válidas, columnas_numéricas, where_sql, where_params, valores_cross_tab)."""
    ds = query.requerir_listo(db, dataset_id, company_id)
    valid = query.columnas_validas(ds)
    numeric = _numeric_columns(ds)
    where_sql, where_params = query_builder.build_where(spec.filter, valid)
    pivot_values = _distinct_pivot_values(company_id, dataset_id, spec, valid)
    return ds, valid, numeric, where_sql, where_params, pivot_values


def _distinct_pivot_values(
    company_id: int, dataset_id: int, spec: PivotSpec, valid: set[str],
) -> list | None:
    """Valores distintos de la columna de cross-tab (o None si no hay cross-tab)."""
    if spec.pivot_column is None:
        return None
    column_sql = query_builder.quote_column(spec.pivot_column, valid)
    values, truncated = duckdb_engine.distinct_values(
        query.locator_parquet(company_id, dataset_id), column_sql, None,
        pivot_builder.MAX_PIVOT_COLUMNS,
    )
    if truncated:
        raise query_builder.InvalidFilterError(
            f"La columna '{spec.pivot_column}' tiene demasiados valores distintos para "
            f"usarla como columnas (máximo {pivot_builder.MAX_PIVOT_COLUMNS}). Filtra o elige otra."
        )
    return values


# ── Guardar como dataset derivado ────────────────────────────────────────

def crear_derivado(
    db: Session, source_id: int, company_id: int, created_by_id: int,
    nombre: str, origin: DatasetOrigin,
) -> DatasetModel:
    """Valida el origen (debe estar listo) y crea el registro derivado en PROCESSING.
    La materialización se despacha aparte (`analysis_dispatch.encolar_materializacion`)."""
    query.requerir_listo(db, source_id, company_id)
    return SqlDatasetRepository(db).create_derived(
        company_id=company_id, created_by_id=created_by_id,
        name=nombre.strip(), origin=origin,
    )


def planificar(
    db: Session, source_id: int, company_id: int, new_id: int, spec: dict,
) -> dict:
    """Lee los metadatos del origen y arma el payload de materialización (SQL + locators).

    Corre en el web (hilo de fondo); el payload resultante es picklable y se envía al
    subproceso. `spec` trae la operación y sus piezas de dominio (filtro + específicas)."""
    source = query.locator_parquet(company_id, source_id)
    dest = query.locator_parquet(company_id, new_id)
    kind = spec["kind"]

    if kind == DatasetOrigin.pivot.value:
        return _plan_pivot(db, source_id, company_id, source, dest, spec["pivot_spec"])

    ds = query.requerir_listo(db, source_id, company_id)
    valid = query.columnas_validas(ds)
    where_sql, where_params = query_builder.build_where(spec.get("filtro"), valid)

    if kind == DatasetOrigin.computed.value:
        select_sql, params = expression_builder.build_compute_select(spec["columnas"], valid)
        return _payload_materialize(source, dest, select_sql, where_sql, params + where_params)
    if kind == DatasetOrigin.replaced.value:
        column_order = [c["name"] for c in (ds.columns or [])]
        select_sql, params = replace_builder.build_replace_select(
            spec["replacements"], column_order, valid
        )
        return _payload_materialize(source, dest, select_sql, where_sql, params + where_params)
    if kind == DatasetOrigin.deduped.value:
        select_sql, qualify_sql = dedupe_builder.build_dedupe(spec["key_columns"], valid)
        return {
            "kind": "dedupe", "source": source, "dest": dest,
            "select_sql": select_sql, "where_sql": where_sql,
            "qualify_sql": qualify_sql, "params": where_params,
        }
    raise ValueError(f"Operación de materialización no soportada: {kind}")


def _plan_pivot(
    db: Session, source_id: int, company_id: int, source: str, dest: str, spec: PivotSpec,
) -> dict:
    """Payload de materialización de un pivote (valida y arma el SQL del reporte)."""
    _, valid, numeric, where_sql, where_params, pivot_values = _pivot_context(
        db, source_id, company_id, spec
    )
    select_sql, group_cols_sql, order_sql, params = pivot_builder.build_pivot(
        spec, valid, numeric, pivot_values
    )
    return {
        "kind": "pivot", "source": source, "dest": dest,
        "select_sql": select_sql, "group_cols_sql": group_cols_sql,
        "where_sql": where_sql, "order_sql": order_sql, "params": params + where_params,
    }


def _payload_materialize(
    source: str, dest: str, select_sql: str, where_sql: str, params: list
) -> dict:
    return {
        "kind": "materialize", "source": source, "dest": dest,
        "select_sql": select_sql, "where_sql": where_sql, "params": params,
    }


def materializar(payload: dict) -> dict:
    """Target picklable del subproceso: ejecuta el COPY pesado al Parquet destino y
    devuelve un resumen liviano (columnas + nº de filas). No toca la BD; lanza ante error."""
    kind = payload["kind"]
    if kind == "pivot":
        duckdb_engine.pivot_to_parquet(
            payload["source"], payload["select_sql"], payload["group_cols_sql"],
            payload["where_sql"], payload["params"], payload["dest"], payload["order_sql"],
        )
    elif kind == "materialize":  # columnas calculadas y buscar/reemplazar
        duckdb_engine.materialize_to_parquet(
            payload["source"], payload["select_sql"], payload["where_sql"],
            payload["params"], payload["dest"],
        )
    elif kind == "dedupe":
        duckdb_engine.dedupe_to_parquet(
            payload["source"], payload["select_sql"], payload["where_sql"],
            payload["qualify_sql"], payload["params"], payload["dest"],
        )
    else:  # defensa: nunca debería ocurrir (kind lo fija `planificar`)
        raise ValueError(f"Tipo de materialización no soportado: {kind}")

    dest = payload["dest"]
    columns = duckdb_engine.get_schema(dest)
    row_count = duckdb_engine.count_matches(dest, "", [])
    if not columns or row_count == 0:
        raise ValueError("No se generó ninguna fila.")
    return {
        "columns": [{"name": c.name, "type": c.type} for c in columns],
        "row_count": row_count,
    }
