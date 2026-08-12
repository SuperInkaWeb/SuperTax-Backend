"""
Orquestación del procesamiento de una conciliación (camino principal):
token SUNAT → solicitar propuesta → descargar → procesar (motor) → guardar.

Las funciones avanzadas del sistema original (compras "sin SIRE" multi-mes,
reanudar y reutilizar propuesta fresca) se incorporarán después; aquí está el
flujo base que deja SIRE funcional de punta a punta.
"""
import asyncio
import logging
import os
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.sire.domain.entities import TipoLibro
from src.modules.sire.infrastructure.models import (
    ReconciliationJobModel,
    SireCredentialsModel,
)
from src.modules.sire.infrastructure.reconciliation.worker import procesar_conciliacion
from src.modules.sire.infrastructure.repositories import SqlReconciliationRepository
from src.modules.sire.infrastructure.sunat import compras as sunat_compras
from src.modules.sire.infrastructure.sunat import ventas as sunat_ventas
from src.modules.sire.infrastructure.sunat.auth import get_sunat_token
from src.platform.database.base import utcnow
from src.platform.tenancy.models import Company

logger = logging.getLogger("sire.orchestrator")


async def procesar_job(db: Session, job_id: int) -> None:
    repo = SqlReconciliationRepository(db)
    job = db.get(ReconciliationJobModel, job_id)
    if job is None:
        return

    company = db.get(Company, job.company_id)
    creds = db.scalar(
        select(SireCredentialsModel).where(
            SireCredentialsModel.company_id == job.company_id
        )
    )
    if company is None or creds is None:
        repo.mark_error(job_id, "Faltan la empresa o las credenciales SUNAT")
        return

    company_id = job.company_id
    ruc = company.ruc
    periodo = job.periodo
    tipo_libro = job.tipo_libro

    creds_snapshot = SimpleNamespace(
        client_id=creds.client_id,
        client_secret_enc=creds.client_secret_enc,
        clave_sol_enc=creds.clave_sol_enc,
        usuario_sol=creds.usuario_sol,
    )

    async def get_token(force_refresh: bool = False) -> str:
        return await get_sunat_token(company_id, creds_snapshot, ruc, force_refresh)

    if tipo_libro == TipoLibro.compras:
        solicitar = sunat_compras.solicitar_export_compras
        descargar = sunat_compras.descargar_ticket_compras
    else:
        solicitar = sunat_ventas.solicitar_export_ventas
        descargar = sunat_ventas.descargar_ticket_ventas

    sunat_tmp_path = None
    try:
        num_ticket = await solicitar(get_token, periodo)
        sunat_tmp_path = await descargar(get_token, num_ticket, periodo)

        payload = {
            "empresa_file_path": job.empresa_file_path,
            "empresa_filename": job.empresa_filename or "",
            "sunat_tmp_path": sunat_tmp_path,
            "tipo_libro": tipo_libro.value,
            "mapeo_config": None,
            "saved_mapping": None,
            "cobertura_fechas": None,
            "cobertura_desc": None,
            "sin_sire": False,
            "sunat_extra_paths": {},
            "sunat_extra_fallidos": [],
            "ruc": ruc,
            "empresa_nombre": company.razon_social,
            "periodo": periodo,
            "propuesta_origen_at": utcnow(),
            "company_id": company_id,
            "job_id": job_id,
        }
        # El motor (pandas/openpyxl) es CPU-bound; se corre en un hilo aparte
        # para no bloquear el loop asíncrono del worker.
        result = await asyncio.to_thread(procesar_conciliacion, payload)
        repo.save_success(job_id, result)
    except Exception as exc:  # se registra y se refleja como error en el job
        logger.exception("Job de conciliación #%s falló", job_id)
        mensaje = (
            str(exc)
            if isinstance(exc, (ValueError, TimeoutError))
            else "Ocurrió un error inesperado al procesar la conciliación."
        )
        repo.mark_error(job_id, mensaje)
    finally:
        if sunat_tmp_path:
            try:
                os.remove(sunat_tmp_path)
            except OSError:
                pass
