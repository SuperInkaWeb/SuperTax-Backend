"""
Lógica de negocio de los jobs de descarga SUNAT: arma la configuración, resuelve
el Excel (subido / caché de preview) y lanza el job.

No sabe de HTTP; la API traduce `SunatJobError` a 400.
"""
import json
import os
import re

from sqlalchemy.orm import Session

from src.modules.sunat.application import credentials as sunat_creds
from src.modules.sunat.infrastructure import input_parser, job_queue
from src.modules.sunat.infrastructure import jobs as runner
from src.modules.sunat.infrastructure.repositories import (
    SqlDriveTokenRepository,
    SqlSunatCredentialsRepository,
)
from src.platform.security import decrypt_field
from src.platform.storage.base import FileStorage

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SunatJobError(Exception):
    """Error de negocio de un job (se traduce a 400 en la API)."""


def _es_email_valido(email: str) -> bool:
    return bool(_EMAIL_RE.match(email or ""))


def _safe_unlink(ruta: str) -> None:
    try:
        os.unlink(ruta)
    except OSError:
        pass


def _comp_dict(c: input_parser.ComprobanteEntrada) -> dict:
    """Comprobante en el shape que consume la UI de selección."""
    return {"id": c.id, "ruc": c.ruc, "tipo": c.tipo_texto, "serie": c.serie, "numero": c.numero}


def _mapeo_dict(m: input_parser.MapeoEntrada) -> dict:
    """Mapeo detectado en el shape que consume la UI de columnas."""
    return {
        "col_ruc": m.col_ruc,
        "col_tipo": m.col_tipo,
        "col_serie": m.col_serie,
        "col_numero": m.col_numero,
    }


def _normalizar_a_canonico(ruta: str, mapeo_manual: dict | None = None) -> str:
    """Lee la entrada (cualquier formato), la mapea y escribe el xlsx canónico que
    consume `automatizar`. Consume `ruta` (la elimina). Lanza SunatJobError si no
    se pueden identificar las columnas o no hay comprobantes válidos."""
    with open(ruta, "rb") as f:
        content = f.read()
    mapeo = input_parser.analizar(content, mapeo_manual)["mapeo"]
    if not mapeo.is_usable:
        _safe_unlink(ruta)
        raise SunatJobError(
            "; ".join(mapeo.warnings) or "No se pudieron identificar las columnas del archivo"
        )
    try:
        comprobantes = input_parser.extraer_comprobantes(content, mapeo)
    except ValueError as exc:
        _safe_unlink(ruta)
        raise SunatJobError(str(exc))
    _safe_unlink(ruta)
    if not comprobantes:
        raise SunatJobError("No se encontraron comprobantes válidos en el archivo")
    return runner.escribir_tmp(input_parser.a_excel_canonico(comprobantes))


def _resolver_login(
    db: Session, company_id: int, ruc: str, usuario: str, clave: str
) -> tuple[str, str, str]:
    """Si el form no trae clave, usa las credenciales SOL guardadas de la empresa."""
    if clave:
        return ruc, usuario, clave
    saved = sunat_creds.get_saved_login(SqlSunatCredentialsRepository(db), company_id)
    if saved is None:
        raise SunatJobError("Ingresa la clave SOL o guárdala en la pestaña Credenciales")
    return saved


def _drive_tokens(db: Session, company_id: int) -> tuple[str, str]:
    token = SqlDriveTokenRepository(db).get(company_id)
    if token is None:
        return "", ""
    access = decrypt_field(token.access_token_enc) if token.access_token_enc else ""
    refresh = decrypt_field(token.refresh_token_enc) if token.refresh_token_enc else ""
    return access, refresh


async def _resolver_excel(preview_id, excel) -> str:
    # El preview_id ya apunta a un xlsx canónico (lo produjo previsualizar()).
    excel_path = runner.consumir_preview(preview_id) if preview_id else None
    if excel_path:
        return excel_path
    # Sin preview: se subió el archivo directo → normalizar a canónico (auto-detección).
    try:
        ruta = await runner.excel_a_tmp(excel)
    except ValueError as exc:
        raise SunatJobError(str(exc))
    return _normalizar_a_canonico(ruta)


async def iniciar(
    db: Session,
    storage: FileStorage,
    company_id: int,
    user_id: int,
    *,
    ruc, usuario, clave, usar_correo, gmail_user, gmail_pass, destino, modo_correo,
    usar_drive, descargar_pdf, descargar_xml,
    comprobantes_seleccionados, preview_id, excel,
) -> str:
    if usar_correo and not _es_email_valido(destino):
        raise SunatJobError("El correo de destino no tiene un formato válido")

    ruc, usuario, clave = _resolver_login(db, company_id, ruc, usuario, clave)
    drive_access, drive_refresh = _drive_tokens(db, company_id)
    excel_path = await _resolver_excel(preview_id, excel)
    config = {
        "ruc": ruc,
        "usuario": usuario,
        "clave": clave,
        "usar_correo": usar_correo,
        "gmail_user": gmail_user,
        "gmail_pass": gmail_pass,
        "destino": destino,
        "modo_correo": modo_correo,
        "usar_drive": usar_drive,
        "drive_access_token": drive_access,
        "drive_refresh_token": drive_refresh,
        "descargar_pdf": descargar_pdf,
        "descargar_xml": descargar_xml,
        "comprobantes_seleccionados": comprobantes_seleccionados,
    }
    job_id = job_queue.encolar_job(db, storage, company_id, user_id, config, excel_path)
    job_queue.encolar_ejecucion(job_id)  # ejecución on-demand (sin worker que sondea)
    return job_id


async def forzar_faltantes(
    db: Session,
    storage: FileStorage,
    company_id: int,
    user_id: int,
    *,
    ruc, usuario, clave, usar_correo, gmail_user, gmail_pass, destino, modo_correo,
    usar_drive, resultados_previos, excel,
) -> str:
    if usar_correo and not _es_email_valido(destino):
        raise SunatJobError("El correo de destino no tiene un formato válido")
    ruc, usuario, clave = _resolver_login(db, company_id, ruc, usuario, clave)
    try:
        previos = json.loads(resultados_previos)
    except (ValueError, TypeError):
        raise SunatJobError("resultados_previos no es JSON válido")

    solo_faltantes = [
        {
            "id": r["id"],
            "descargar_pdf": r.get("pide_pdf", True) and not r.get("pdf", True),
            "descargar_xml": r.get("pide_xml", True) and not r.get("xml", True),
        }
        for r in previos
        if r.get("estado") in ("Parcial", "Error")
    ]
    if not solo_faltantes:
        raise SunatJobError("No hay comprobantes faltantes para reintentar")

    drive_access, drive_refresh = _drive_tokens(db, company_id)
    try:
        ruta = await runner.excel_a_tmp(excel)
    except ValueError as exc:
        raise SunatJobError(str(exc))
    excel_path = _normalizar_a_canonico(ruta)

    config = {
        "ruc": ruc,
        "usuario": usuario,
        "clave": clave,
        "usar_correo": usar_correo,
        "gmail_user": gmail_user,
        "gmail_pass": gmail_pass,
        "destino": destino,
        "modo_correo": modo_correo,
        "usar_drive": usar_drive,
        "drive_access_token": drive_access,
        "drive_refresh_token": drive_refresh,
        "solo_faltantes": solo_faltantes,
    }
    job_id = job_queue.encolar_job(db, storage, company_id, user_id, config, excel_path)
    job_queue.encolar_ejecucion(job_id)  # ejecución on-demand (sin worker que sondea)
    return job_id


async def previsualizar(
    db: Session, company_id: int, *, excel, mapeo_manual: dict | None = None
) -> dict:
    """Analiza la entrada (cualquier formato), propone el mapeo de columnas y, si es
    usable, devuelve los comprobantes + un preview_id que apunta al xlsx canónico.

    Si el mapeo no es confiable, devuelve cabeceras + muestra + mapeo detectado para
    que la UI lo corrija (y vuelva a llamar con `mapeo_manual`). Contrato tipo SIRE.
    """
    try:
        ruta = await runner.excel_a_tmp(excel)
    except ValueError as exc:
        raise SunatJobError(str(exc))

    try:
        with open(ruta, "rb") as f:
            content = f.read()
        analisis = input_parser.analizar(content, mapeo_manual)
    except Exception as exc:
        _safe_unlink(ruta)
        raise SunatJobError(f"No se pudo leer el archivo: {exc}")

    mapeo = analisis["mapeo"]
    respuesta = {
        "mapeo": _mapeo_dict(mapeo),
        "headers": analisis["headers"],
        "muestra": analisis["muestra"],
        "confianza": analisis["confianza"],
        "necesita_revision": analisis["necesita_revision"],
        "comprobantes": [],
        "preview_id": "",
    }
    if not mapeo.is_usable:
        _safe_unlink(ruta)
        return respuesta

    try:
        comprobantes = input_parser.extraer_comprobantes(content, mapeo)
    except ValueError as exc:
        _safe_unlink(ruta)
        raise SunatJobError(str(exc))
    _safe_unlink(ruta)  # el crudo ya no se necesita; el job usará el canónico

    if not comprobantes:
        respuesta["necesita_revision"] = True
        return respuesta

    canonico = runner.escribir_tmp(input_parser.a_excel_canonico(comprobantes))
    respuesta["comprobantes"] = [_comp_dict(c) for c in comprobantes]
    respuesta["preview_id"] = runner.guardar_preview(canonico)
    return respuesta
