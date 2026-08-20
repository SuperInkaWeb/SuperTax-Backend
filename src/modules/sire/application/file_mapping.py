"""
Casos de uso del formato de archivo (mapeo de columnas) de la empresa por libro.

Envuelven las funciones puras del parser (analizar/validar) y el repositorio del
formato guardado. Una conciliación usa el formato guardado si existe; solo el
apartado "Formato de archivo" (o el checkbox explícito) lo persiste.
"""
from src.modules.sire.infrastructure.models import CompanyFileMappingModel
from src.modules.sire.infrastructure.parser.empresa_file import normalizar_content
from src.modules.sire.infrastructure.parser.mapeo import (
    analizar_archivo,
    validar_mapeo,
)
from src.modules.sire.infrastructure.repositories import SqlFileMappingRepository

LIBROS_VALIDOS = ("compras", "ventas")


def mapping_a_config(m: CompanyFileMappingModel) -> dict:
    return {
        "delimiter": m.delimiter,
        "encoding": m.encoding,
        "has_header": m.has_header,
        "skip_rows": m.skip_rows,
        "serie_numero_combinado": m.serie_numero_combinado,
        "columnas": m.columnas or {},
    }


def _saved_response(m: CompanyFileMappingModel | None) -> dict | None:
    if not m or not m.columnas or not m.confirmed_by_user:
        return None
    return {
        "tipo_libro": m.tipo_libro,
        **mapping_a_config(m),
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


def analizar(
    repo: SqlFileMappingRepository, company_id: int, tipo_libro: str, content: bytes,
    skip_rows: int | None = None,
) -> dict:
    content = normalizar_content(content)  # Excel → texto; CSV/TXT pasa sin cambios
    saved = repo.get(company_id, tipo_libro)
    saved_config = (
        mapping_a_config(saved)
        if (saved and saved.columnas and saved.confirmed_by_user)
        else None
    )
    resultado = analizar_archivo(content, tipo_libro, saved_config, skip_rows)
    if "error" in resultado:
        raise ValueError(resultado["error"])
    resultado["tiene_guardado"] = saved_config is not None
    return resultado


def validar(content: bytes, cfg: dict, tipo_libro: str) -> dict:
    return validar_mapeo(normalizar_content(content), cfg, tipo_libro)


def guardar(
    repo: SqlFileMappingRepository,
    company_id: int,
    tipo_libro: str,
    cfg: dict,
    content: bytes,
) -> dict | None:
    content = normalizar_content(content)
    val = validar_mapeo(content, cfg, tipo_libro)
    if not val["ok"]:
        detalle = (
            "; ".join(val["avisos"])
            if val.get("avisos")
            else f"faltan campos: {val.get('faltantes')}"
        )
        raise ValueError(f"El mapeo no supera la validación: {detalle}")
    return _saved_response(repo.save(company_id, tipo_libro, cfg))


def get_saved(repo: SqlFileMappingRepository, company_id: int, tipo_libro: str) -> dict | None:
    return _saved_response(repo.get(company_id, tipo_libro))


def delete_saved(repo: SqlFileMappingRepository, company_id: int, tipo_libro: str) -> None:
    repo.delete(company_id, tipo_libro)
