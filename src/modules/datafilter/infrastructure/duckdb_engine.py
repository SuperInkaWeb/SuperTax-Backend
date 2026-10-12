"""Acceso a datos vía DuckDB sobre archivos Parquet (en disco local o en R2/S3).

Portado del DataFilter standalone (motor verbatim, excluido de ruff). Responsabilidad
única: ejecutar contra DuckDB. No conoce reglas de negocio.

Un "locator" es una cadena que apunta al archivo: o una ruta local, o una URI
`s3://bucket/clave` (Cloudflare R2). DuckDB lee/escribe ambos de forma transparente
gracias a la extensión httpfs, que se configura en `_connect()` cuando el storage es S3.

Notas de seguridad:
  - Los locators los genera el servidor (company_id/dataset_id, claves fijas), nunca
    datos del usuario; se escapan/normalizan antes de inyectarse como literal SQL.
  - Los VALORES de filtro se pasan SIEMPRE como parámetros ('?') enlazados.
"""
import logging
import os
import tempfile
import zipfile
from dataclasses import dataclass
from urllib.parse import urlparse
from xml.etree import ElementTree

import duckdb

from src.platform.config.settings import settings

logger = logging.getLogger("datafilter.duckdb")

CSV_EXTENSIONS = {".csv", ".txt"}
EXCEL_EXTENSIONS = {".xlsx", ".xls"}

_XLSX_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


@dataclass(slots=True)
class ColumnInfo:
    name: str
    type: str


class EncodingNotSupported(Exception):
    """El archivo no es UTF-8 ni latin-1 legible por DuckDB (típico Windows-1252).

    La señala la capa de datos para que el servicio lo transcodifique a UTF-8; DuckDB
    nativo solo soporta utf-8/utf-16/latin-1 y su latin-1 rechaza el rango de control C1.
    """


def _sql_path(locator: str) -> str:
    """Literal SQL seguro para un locator controlado por el servidor (ruta o s3://)."""
    normalized = str(locator).replace("\\", "/").replace("'", "''")
    return f"'{normalized}'"


def _sql_str(value: str) -> str:
    """Literal SQL seguro para una cadena (escapa comillas simples)."""
    return "'" + value.replace("'", "''") + "'"


def _reader_expr(
    source: str, extension: str, sheet: str | None = None,
    encoding: str | None = None, null_padding: bool = False, raw_text: bool = False,
) -> str:
    """Expresión de tabla DuckDB para leer el archivo según su extensión.

    - CSV/TXT: auto-detecta delimitador, tipos y cabecera. `encoding` fuerza la
      codificación. `null_padding` rellena con NULL las filas con menos columnas.
      `raw_text` desactiva las comillas y lee todo como texto (último recurso SUNAT).
    - Excel: lee la hoja indicada (o la primera) asumiendo cabecera.
    """
    ext = extension.lower()
    literal = _sql_path(source)
    if ext in CSV_EXTENSIONS:
        options = ""
        if encoding:
            options += f", encoding = {_sql_str(encoding)}"
        if null_padding:
            options += ", null_padding = true"
        if raw_text:
            options += ", quote = '', all_varchar = true"
        return f"read_csv_auto({literal}{options})"
    if ext in EXCEL_EXTENSIONS:
        sheet_clause = f", sheet = {_sql_str(sheet)}" if sheet else ""
        return f"read_xlsx({literal}, header = true{sheet_clause})"
    raise ValueError(f"Extensión no soportada: {ext}")


def _is_encoding_error(exc: Exception) -> bool:
    message = str(exc).lower()
    hints = ("utf-8", "unicode", "byte sequence", "encoding", "encoded")
    return any(hint in message for hint in hints)


def list_xlsx_sheets(local_path: str) -> list[str]:
    """Nombres de hoja de un .xlsx LOCAL leyendo su workbook.xml (sin dependencias)."""
    if not zipfile.is_zipfile(local_path):
        return []
    try:
        with zipfile.ZipFile(local_path) as archive:
            root = ElementTree.fromstring(archive.read("xl/workbook.xml"))
    except (KeyError, zipfile.BadZipFile, ElementTree.ParseError):
        return []
    namespace = {"main": _XLSX_MAIN_NS}
    return [
        name
        for sheet in root.findall(".//main:sheets/main:sheet", namespace)
        if (name := sheet.get("name"))
    ]


def _apply_memory_guards(con: duckdb.DuckDBPyConnection) -> None:
    """Acota la RAM de DuckDB y habilita el volcado a disco (anti-OOM en contenedor)."""
    if settings.DUCKDB_MEMORY_LIMIT:
        con.execute(f"SET memory_limit={_sql_str(settings.DUCKDB_MEMORY_LIMIT)}")
    temp_dir = settings.DUCKDB_TEMP_DIR or os.path.join(tempfile.gettempdir(), "datafilter_duckdb_tmp")
    os.makedirs(temp_dir, exist_ok=True)
    con.execute(f"SET temp_directory={_sql_str(temp_dir)}")


def _connect() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("SET preserve_insertion_order = false")
    _apply_memory_guards(con)
    try:
        con.execute("INSTALL excel")
        con.execute("LOAD excel")
    except duckdb.Error:
        pass  # sin excel: CSV/TXT siguen funcionando

    if settings.STORAGE_BACKEND == "s3":
        # httpfs permite leer/escribir s3://... (R2 es compatible con S3).
        host = urlparse(settings.S3_ENDPOINT_URL).netloc or settings.S3_ENDPOINT_URL
        con.execute("INSTALL httpfs")
        con.execute("LOAD httpfs")
        con.execute(f"SET s3_endpoint={_sql_str(host)}")
        con.execute(f"SET s3_access_key_id={_sql_str(settings.S3_ACCESS_KEY)}")
        con.execute(f"SET s3_secret_access_key={_sql_str(settings.S3_SECRET_KEY)}")
        con.execute(f"SET s3_region={_sql_str(settings.S3_REGION or 'auto')}")
        con.execute("SET s3_url_style='path'")
        con.execute(
            f"SET s3_use_ssl={'false' if settings.S3_ENDPOINT_URL.startswith('http://') else 'true'}"
        )
    return con


def convert_to_parquet(
    source: str, dest: str, extension: str, sheet: str | None = None
) -> tuple[list[ColumnInfo], int]:
    """Convierte el archivo original a Parquet columnar. Devuelve (columnas, filas).

    La conversión se hace vía COPY en streaming: DuckDB no carga todo en RAM.
    `sheet` solo aplica a Excel; para CSV/TXT se ignora.
    """
    with _connect() as con:
        _copy_with_fallbacks(con, source, dest, extension, sheet)
        columns = _describe(con, dest)
        row_count = con.execute(f"SELECT count(*) FROM read_parquet({_sql_path(dest)})").fetchone()[0]
    return columns, int(row_count)


def _copy_with_fallbacks(
    con: duckdb.DuckDBPyConnection, source: str, dest: str,
    extension: str, sheet: str | None,
) -> None:
    """Convierte a Parquet tolerando codificación irregular y estructura de CSV."""
    try:
        _copy_trying_encodings(con, source, dest, extension, sheet)
        return
    except duckdb.Error:
        if extension.lower() not in CSV_EXTENSIONS:
            raise

    logger.warning("CSV con estructura irregular en %s: reintento con null_padding", dest)
    try:
        _copy_trying_encodings(con, source, dest, extension, sheet, null_padding=True)
        return
    except duckdb.Error:
        pass

    logger.warning("CSV aún inválido en %s: reintento sin comillas y como texto", dest)
    _copy_trying_encodings(con, source, dest, extension, sheet, null_padding=True, raw_text=True)


def _copy_trying_encodings(
    con: duckdb.DuckDBPyConnection, source: str, dest: str, extension: str,
    sheet: str | None, null_padding: bool = False, raw_text: bool = False,
) -> None:
    """Copia a Parquet probando UTF-8 y, si falla por codificación, latin-1."""
    try:
        _copy_to_parquet(con, source, dest, extension, sheet, None, null_padding, raw_text)
        return
    except duckdb.Error as exc:
        if not (extension.lower() in CSV_EXTENSIONS and _is_encoding_error(exc)):
            raise
    try:
        _copy_to_parquet(con, source, dest, extension, sheet, "latin-1", null_padding, raw_text)
    except duckdb.Error as exc:
        if _is_encoding_error(exc):
            raise EncodingNotSupported(str(exc)) from exc
        raise


def _copy_to_parquet(
    con: duckdb.DuckDBPyConnection, source: str, dest: str, extension: str,
    sheet: str | None, encoding: str | None, null_padding: bool = False, raw_text: bool = False,
) -> None:
    reader = _reader_expr(source, extension, sheet, encoding, null_padding, raw_text)
    con.execute(f"COPY (SELECT * FROM {reader}) TO {_sql_path(dest)} (FORMAT PARQUET)")


def _describe(con: duckdb.DuckDBPyConnection, parquet: str) -> list[ColumnInfo]:
    rows = con.execute(f"DESCRIBE SELECT * FROM read_parquet({_sql_path(parquet)})").fetchall()
    # DESCRIBE => (column_name, column_type, null, key, default, extra)
    return [ColumnInfo(name=row[0], type=row[1]) for row in rows]


def get_schema(parquet: str) -> list[ColumnInfo]:
    with _connect() as con:
        return _describe(con, parquet)


def count_matches(parquet: str, where_sql: str, params: list) -> int:
    where_clause = f"WHERE {where_sql}" if where_sql else ""
    sql = f"SELECT count(*) FROM read_parquet({_sql_path(parquet)}) {where_clause}"
    with _connect() as con:
        return int(con.execute(sql, params).fetchone()[0])


def preview(
    parquet: str, select_sql: str, where_sql: str, order_sql: str,
    limit: int, offset: int, params: list,
) -> tuple[list[str], list[dict]]:
    """Devuelve (nombres_columna, filas_como_dict) para una página de resultados."""
    where_clause = f"WHERE {where_sql}" if where_sql else ""
    sql = (
        f"SELECT {select_sql} FROM read_parquet({_sql_path(parquet)}) "
        f"{where_clause} {order_sql} LIMIT {int(limit)} OFFSET {int(offset)}"
    )
    with _connect() as con:
        cursor = con.execute(sql, params)
        column_names = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
    return column_names, [dict(zip(column_names, row)) for row in rows]


def _copy_options(fmt: str, delimiter: str | None) -> str:
    if fmt == "csv":
        return "(FORMAT CSV, HEADER true)"
    if fmt == "txt":
        return f"(FORMAT CSV, DELIMITER {_sql_str(delimiter or chr(9))}, HEADER true)"
    if fmt == "xlsx":
        return "(FORMAT xlsx, HEADER true)"
    raise ValueError(f"Formato no soportado: {fmt}")


def _copy_query_to_file(inner_sql: str, params: list, dest_path: str, fmt: str, delimiter: str | None) -> None:
    sql = f"COPY ({inner_sql}) TO {_sql_path(dest_path)} {_copy_options(fmt, delimiter)}"
    with _connect() as con:
        con.execute(sql, params)


def export_to_file(
    parquet: str, select_sql: str, where_sql: str, order_sql: str,
    params: list, dest_path: str, fmt: str, delimiter: str | None = None,
) -> None:
    """Exporta una proyección filtrada (SELECT ... WHERE ... ORDER BY) a un archivo LOCAL."""
    where_clause = f"WHERE {where_sql}" if where_sql else ""
    inner = (
        f"SELECT {select_sql} FROM read_parquet({_sql_path(parquet)}) "
        f"{where_clause} {order_sql}"
    )
    _copy_query_to_file(inner, params, dest_path, fmt, delimiter)


def _pivot_body(
    parquet: str, select_sql: str, where_sql: str, group_cols_sql: str,
    order_sql: str | None = None,
) -> str:
    where_clause = f"WHERE {where_sql}" if where_sql else ""
    return (
        f"SELECT {select_sql} FROM read_parquet({_sql_path(parquet)}) "
        f"{where_clause} GROUP BY {group_cols_sql} ORDER BY {order_sql or group_cols_sql}"
    )


def pivot_preview(
    parquet: str, select_sql: str, group_cols_sql: str, where_sql: str,
    params: list, limit: int, offset: int, order_sql: str | None = None,
) -> tuple[list[str], list[dict]]:
    body = _pivot_body(parquet, select_sql, where_sql, group_cols_sql, order_sql)
    sql = f"SELECT * FROM ({body}) LIMIT {int(limit)} OFFSET {int(offset)}"
    with _connect() as con:
        cursor = con.execute(sql, params)
        column_names = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
    return column_names, [dict(zip(column_names, row)) for row in rows]


def pivot_totals(parquet: str, select_sql: str, where_sql: str, params: list) -> dict:
    where_clause = f"WHERE {where_sql}" if where_sql else ""
    sql = f"SELECT {select_sql} FROM read_parquet({_sql_path(parquet)}) {where_clause}"
    with _connect() as con:
        cursor = con.execute(sql, params)
        column_names = [desc[0] for desc in cursor.description]
        row = cursor.fetchone()
    return dict(zip(column_names, row)) if row else {}


def pivot_count(parquet: str, group_cols_sql: str, where_sql: str, where_params: list) -> int:
    where_clause = f"WHERE {where_sql}" if where_sql else ""
    inner = (
        f"SELECT {group_cols_sql} FROM read_parquet({_sql_path(parquet)}) "
        f"{where_clause} GROUP BY {group_cols_sql}"
    )
    with _connect() as con:
        return int(con.execute(f"SELECT count(*) FROM ({inner})", where_params).fetchone()[0])


def pivot_to_parquet(
    parquet: str, select_sql: str, group_cols_sql: str, where_sql: str,
    params: list, dest: str, order_sql: str | None = None,
) -> None:
    body = _pivot_body(parquet, select_sql, where_sql, group_cols_sql, order_sql)
    with _connect() as con:
        con.execute(f"COPY ({body}) TO {_sql_path(dest)} (FORMAT PARQUET)", params)


def pivot_export_to_file(
    parquet: str, select_sql: str, group_cols_sql: str, where_sql: str,
    params: list, dest_path: str, fmt: str, delimiter: str | None = None,
    order_sql: str | None = None,
) -> None:
    body = _pivot_body(parquet, select_sql, where_sql, group_cols_sql, order_sql)
    _copy_query_to_file(body, params, dest_path, fmt, delimiter)


def _dedupe_body(parquet: str, select_sql: str, where_sql: str, qualify_sql: str | None) -> str:
    where_clause = f"WHERE {where_sql}" if where_sql else ""
    qualify_clause = f"QUALIFY {qualify_sql}" if qualify_sql else ""
    return (
        f"SELECT {select_sql} FROM read_parquet({_sql_path(parquet)}) "
        f"{where_clause} {qualify_clause}"
    )


def dedupe_preview(
    parquet: str, select_sql: str, where_sql: str, qualify_sql: str | None,
    params: list, limit: int, offset: int, order_sql: str = "",
) -> tuple[list[str], list[dict]]:
    body = _dedupe_body(parquet, select_sql, where_sql, qualify_sql)
    sql = f"SELECT * FROM ({body}) {order_sql} LIMIT {int(limit)} OFFSET {int(offset)}"
    with _connect() as con:
        cursor = con.execute(sql, params)
        column_names = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
    return column_names, [dict(zip(column_names, row)) for row in rows]


def dedupe_count(
    parquet: str, select_sql: str, where_sql: str, qualify_sql: str | None, params: list
) -> int:
    body = _dedupe_body(parquet, select_sql, where_sql, qualify_sql)
    with _connect() as con:
        return int(con.execute(f"SELECT count(*) FROM ({body})", params).fetchone()[0])


def dedupe_to_parquet(
    parquet: str, select_sql: str, where_sql: str, qualify_sql: str | None,
    params: list, dest: str,
) -> None:
    body = _dedupe_body(parquet, select_sql, where_sql, qualify_sql)
    with _connect() as con:
        con.execute(f"COPY ({body}) TO {_sql_path(dest)} (FORMAT PARQUET)", params)


def dedupe_export_to_file(
    parquet: str, select_sql: str, where_sql: str, qualify_sql: str | None,
    params: list, dest_path: str, fmt: str, delimiter: str | None = None,
) -> None:
    body = _dedupe_body(parquet, select_sql, where_sql, qualify_sql)
    _copy_query_to_file(body, params, dest_path, fmt, delimiter)


def materialize_to_parquet(
    parquet: str, select_sql: str, where_sql: str, params: list, dest: str,
) -> None:
    where_clause = f"WHERE {where_sql}" if where_sql else ""
    body = f"SELECT {select_sql} FROM read_parquet({_sql_path(parquet)}) {where_clause}"
    with _connect() as con:
        con.execute(f"COPY ({body}) TO {_sql_path(dest)} (FORMAT PARQUET)", params)


def column_stats(
    parquet: str, column_sql: str, where_sql: str, params: list, numeric: bool
) -> tuple:
    where_clause = f"WHERE {where_sql}" if where_sql else ""
    extra = f", round(sum({column_sql}), 6), round(avg({column_sql}), 6)" if numeric else ""
    sql = (
        f"SELECT count(*), count({column_sql}), count(DISTINCT {column_sql}), "
        f"min({column_sql}), max({column_sql}){extra} "
        f"FROM read_parquet({_sql_path(parquet)}) {where_clause}"
    )
    with _connect() as con:
        return con.execute(sql, params).fetchone()


def column_top_values(
    parquet: str, column_sql: str, where_sql: str, params: list, limit: int
) -> list[tuple[str, int]]:
    not_null = f"{column_sql} IS NOT NULL"
    where_clause = f"WHERE {where_sql} AND {not_null}" if where_sql else f"WHERE {not_null}"
    sql = (
        f"SELECT CAST({column_sql} AS VARCHAR) AS value, count(*) AS freq "
        f"FROM read_parquet({_sql_path(parquet)}) {where_clause} "
        f"GROUP BY {column_sql} ORDER BY freq DESC, value LIMIT {int(limit)}"
    )
    with _connect() as con:
        rows = con.execute(sql, params).fetchall()
    return [(str(row[0]), int(row[1])) for row in rows]


def distinct_values(
    parquet: str, column_sql: str, search: str | None, limit: int,
) -> tuple[list, bool]:
    filter_clause = ""
    query_params: list = []
    if search:
        filter_clause = f"AND CAST({column_sql} AS VARCHAR) ILIKE ?"
        query_params.append(f"%{search}%")
    sql = (
        f"SELECT DISTINCT {column_sql} AS value "
        f"FROM read_parquet({_sql_path(parquet)}) "
        f"WHERE {column_sql} IS NOT NULL {filter_clause} "
        f"ORDER BY value LIMIT {int(limit) + 1}"
    )
    with _connect() as con:
        rows = con.execute(sql, query_params).fetchall()
    values = [row[0] for row in rows]
    truncated = len(values) > limit
    return values[:limit], truncated
