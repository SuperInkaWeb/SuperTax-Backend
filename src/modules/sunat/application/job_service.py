"""
Lógica de negocio de los jobs de descarga SUNAT: arma la configuración, resuelve
el Excel (subido / caché de preview / enlace de Drive) y lanza el job.

No sabe de HTTP; la API traduce `SunatJobError` a 400.
"""
import json
import os
import re

from sqlalchemy.orm import Session

from src.modules.sunat.infrastructure import job_queue
from src.modules.sunat.infrastructure import jobs as runner
from src.modules.sunat.infrastructure.repositories import SqlDriveTokenRepository
from src.platform.security import decrypt_field
from src.platform.storage.base import FileStorage

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SunatJobError(Exception):
    """Error de negocio de un job (se traduce a 400 en la API)."""


def _es_email_valido(email: str) -> bool:
    return bool(_EMAIL_RE.match(email or ""))


def _drive_tokens(db: Session, company_id: int) -> tuple[str, str]:
    token = SqlDriveTokenRepository(db).get(company_id)
    if token is None:
        return "", ""
    access = decrypt_field(token.access_token_enc) if token.access_token_enc else ""
    refresh = decrypt_field(token.refresh_token_enc) if token.refresh_token_enc else ""
    return access, refresh


async def _resolver_excel(preview_id, excel, excel_link, drive_access, drive_refresh) -> str:
    excel_path = runner.consumir_preview(preview_id) if preview_id else None
    if excel_path:
        return excel_path
    try:
        return await runner.excel_a_tmp(excel, excel_link, drive_access, drive_refresh)
    except ValueError as exc:
        raise SunatJobError(str(exc))


async def iniciar(
    db: Session,
    storage: FileStorage,
    company_id: int,
    user_id: int,
    *,
    ruc, usuario, clave, usar_correo, gmail_user, gmail_pass, destino, modo_correo,
    usar_drive, drive_folder, excel_link, descargar_pdf, descargar_xml,
    comprobantes_seleccionados, preview_id, excel,
) -> str:
    if usar_correo and not _es_email_valido(destino):
        raise SunatJobError("El correo de destino no tiene un formato válido")

    drive_access, drive_refresh = _drive_tokens(db, company_id)
    excel_path = await _resolver_excel(
        preview_id, excel, excel_link, drive_access, drive_refresh
    )
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
        "drive_folder": drive_folder.strip(),
        "drive_access_token": drive_access,
        "drive_refresh_token": drive_refresh,
        "descargar_pdf": descargar_pdf,
        "descargar_xml": descargar_xml,
        "comprobantes_seleccionados": comprobantes_seleccionados,
    }
    return job_queue.encolar_job(db, storage, company_id, user_id, config, excel_path)


async def forzar_faltantes(
    db: Session,
    storage: FileStorage,
    company_id: int,
    user_id: int,
    *,
    ruc, usuario, clave, usar_correo, gmail_user, gmail_pass, destino, modo_correo,
    usar_drive, drive_folder, excel_link, resultados_previos, excel,
) -> str:
    if usar_correo and not _es_email_valido(destino):
        raise SunatJobError("El correo de destino no tiene un formato válido")
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
        excel_path = await runner.excel_a_tmp(excel, excel_link, drive_access, drive_refresh)
    except ValueError as exc:
        raise SunatJobError(str(exc))

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
        "drive_folder": drive_folder.strip(),
        "drive_access_token": drive_access,
        "drive_refresh_token": drive_refresh,
        "solo_faltantes": solo_faltantes,
    }
    return job_queue.encolar_job(db, storage, company_id, user_id, config, excel_path)


async def previsualizar(db: Session, company_id: int, *, excel, excel_link) -> dict:
    drive_access, drive_refresh = _drive_tokens(db, company_id)
    try:
        ruta = await runner.excel_a_tmp(excel, excel_link, drive_access, drive_refresh)
    except ValueError as exc:
        raise SunatJobError(str(exc))

    from src.modules.sunat.infrastructure.automation import previsualizar_excel

    try:
        comprobantes = previsualizar_excel(ruta)
    except Exception as exc:
        try:
            os.unlink(ruta)
        except OSError:
            pass
        raise SunatJobError(str(exc))
    preview_id = runner.guardar_preview(ruta)  # se conserva; /iniciar lo consume
    return {"comprobantes": comprobantes, "preview_id": preview_id}
