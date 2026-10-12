"""
Ingesta: convertir el archivo subido a Parquet + detectar su esquema.

`convertir` es el target de un PROCESO HIJO efímero (ver `ingest_dispatch`): lee el
crudo, lo convierte a Parquet y devuelve un resumen liviano (columnas, filas, hojas).
No toca la BD; el proceso web persiste el resultado. Portado del DataFilter standalone.
"""
import logging
import os
import tempfile

from src.modules.datafilter.infrastructure import duckdb_engine, storage_access
from src.modules.datafilter.infrastructure.duckdb_engine import (
    EXCEL_EXTENSIONS,
    EncodingNotSupported,
)

logger = logging.getLogger("datafilter.ingest")

# Codificación de reserva para transcodificar lo que DuckDB no lee nativamente.
# SUNAT exporta desde Windows: cp1252 mapea la puntuación del rango C1.
_FALLBACK_ENCODING = "cp1252"
_TRANSCODE_CHUNK = 1024 * 1024  # 1 MiB de caracteres por iteración (memoria plana)


def convertir(payload: dict) -> dict:
    """Target picklable del subproceso: convierte y devuelve el resumen (o lanza)."""
    return _convert(
        payload["company_id"], payload["dataset_id"], payload["extension"], payload.get("sheet")
    )


def _convert(company_id: int, dataset_id: int, extension: str, sheet: str | None) -> dict:
    """Conversión pura original → Parquet. Devuelve el resumen o lanza. Limpia temporales."""
    src_key = storage_access.source_key(company_id, dataset_id, extension)
    dest_locator = storage_access.locator(storage_access.parquet_key(company_id, dataset_id))
    is_excel = extension.lower() in EXCEL_EXTENSIONS
    temp_files: list[str] = []
    sheets: list[str] | None = None
    active_sheet: str | None = None
    try:
        if is_excel:
            local_raw = storage_access.download_to_temp(src_key, suffix=extension.lower())
            if storage_access.es_temporal(local_raw):
                temp_files.append(local_raw)
            sheets = duckdb_engine.list_xlsx_sheets(local_raw)
            active_sheet = sheet if sheet in sheets else (sheets[0] if sheets else None)
            columns, row_count = duckdb_engine.convert_to_parquet(
                local_raw, dest_locator, extension, active_sheet
            )
        else:
            columns, row_count = _ingest_csv(src_key, extension, dest_locator, temp_files)
            storage_access.delete(src_key)  # CSV/TXT: el crudo ya no se usa

        # 0 filas = archivo vacío o solo-cabecera: inservible para filtrar.
        if not columns or row_count == 0:
            raise ValueError("El archivo no contiene filas de datos. Revisa que no esté vacío.")
        return {
            "columns": [{"name": c.name, "type": c.type} for c in columns],
            "row_count": row_count,
            "sheets": sheets,
            "active_sheet": active_sheet,
        }
    finally:
        for path in temp_files:
            if os.path.exists(path):
                os.unlink(path)


def _ingest_csv(
    src_key: str, extension: str, dest_locator: str, temp_files: list[str]
) -> tuple[list, int]:
    """Convierte CSV/TXT a Parquet, transcodificando a UTF-8 cuando hace falta."""
    wide_encoding = _sniff_wide_encoding(src_key)
    if wide_encoding is None:
        source_locator = storage_access.locator(src_key)
        try:
            return duckdb_engine.convert_to_parquet(source_locator, dest_locator, extension)
        except EncodingNotSupported:
            source_encoding = _FALLBACK_ENCODING
            logger.info("Windows-1252 detectado en %s, transcodificando a UTF-8", src_key)
    else:
        source_encoding = wide_encoding
        logger.info("UTF-16 detectado en %s, transcodificando a UTF-8", src_key)

    local_raw = storage_access.download_to_temp(src_key, suffix=extension.lower())
    if storage_access.es_temporal(local_raw):
        temp_files.append(local_raw)
    utf8_path = _transcode_to_utf8(local_raw, source_encoding)
    temp_files.append(utf8_path)
    return duckdb_engine.convert_to_parquet(utf8_path, dest_locator, extension)


def _sniff_wide_encoding(src_key: str) -> str | None:
    """'utf-16' si el archivo trae BOM UTF-16 (que DuckDB leería como basura)."""
    prefix = storage_access.read_prefix(src_key, 2)
    if prefix[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return "utf-16"
    return None


def _transcode_to_utf8(source_path: str, source_encoding: str) -> str:
    """Reescribe un CSV/TXT (Windows-1252 o UTF-16) a UTF-8 en un temporal (streaming)."""
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    handle.close()
    with open(source_path, "r", encoding=source_encoding, errors="replace", newline="") as reader, \
            open(handle.name, "w", encoding="utf-8", newline="") as writer:
        for chunk in iter(lambda: reader.read(_TRANSCODE_CHUNK), ""):
            writer.write(chunk)
    return handle.name


def friendly_error(exc: Exception) -> str:
    """Traduce el fallo técnico a un mensaje accionable en español (sin stack ni rutas)."""
    text = str(exc).lower()
    if any(h in text for h in
           ("utf-8", "utf-16", "latin-1", "encoded", "encoding", "unicode", "byte sequence")):
        return ("No se pudo determinar la codificación del archivo. Vuelve a guardarlo como "
                "UTF-8 (o CSV UTF-8) e inténtalo de nuevo.")
    if any(h in text for h in
           ("delimiter", "sniffing", "columns", "expected", "csv error", "unterminated", "quote")):
        return ("El archivo tiene filas con distinto número de columnas o un formato "
                "inesperado. Revisa que todas las filas usen el mismo separador y número "
                "de columnas.")
    if any(h in text for h in ("xlsx", "zip", "sheet", "excel")):
        return ("No se pudo leer el archivo Excel. Puede estar dañado, protegido con "
                "contraseña o en un formato no compatible. Guárdalo de nuevo como .xlsx.")
    message = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return message[:300]
