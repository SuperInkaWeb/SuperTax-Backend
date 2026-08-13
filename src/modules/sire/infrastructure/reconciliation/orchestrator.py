"""
Orquestación del procesamiento de una conciliación.

Resolución del ticket de la propuesta SUNAT, en orden de preferencia:
  1. Reanudar: si el propio job trae un `num_ticket` fresco (<24h) y vivo, lo retoma.
  2. Reutilizar: si el usuario lo pidió, aprovecha una propuesta fresca y
     "terminada" de OTRO job de la misma empresa/periodo/libro.
  3. Solicitar: si no, pide una nueva a SUNAT.

Compras "sin SIRE" repite esa resolución por cada mes rezagado. Luego descarga,
corre el motor y guarda el resultado.

El camino vivo contra SUNAT requiere credenciales reales; la lógica de decisión
(frescura, reutilización, detección de meses) sí es verificable en tests.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.sire.domain.entities import TipoLibro
from src.modules.sire.infrastructure.models import (
    ReconciliationJobModel,
    SireCredentialsModel,
)
from src.modules.sire.infrastructure.reconciliation.worker import (
    extraer_periodos_emision,
    procesar_conciliacion,
)
from src.modules.sire.infrastructure.repositories import (
    SqlFileMappingRepository,
    SqlReconciliationRepository,
)
from src.modules.sire.infrastructure.sunat import compras as sunat_compras
from src.modules.sire.infrastructure.sunat import ventas as sunat_ventas
from src.modules.sire.infrastructure.sunat.auth import get_sunat_token
from src.platform.database.base import utcnow
from src.platform.tenancy.models import Company

logger = logging.getLogger("sire.orchestrator")

_TICKET_FRESCURA_HORAS = 24


def _es_fresco(momento: datetime | None) -> bool:
    """Un ticket/propuesta sigue vigente si se generó hace menos de 24h."""
    if momento is None:
        return False
    con_tz = momento if momento.tzinfo else momento.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - con_tz < timedelta(hours=_TICKET_FRESCURA_HORAS)


def _ticket_vivo(consulta: tuple[str, object] | None) -> bool:
    """SUNAT sigue reconociendo el ticket si respondió y su estado no es error."""
    return consulta is not None and "error" not in consulta[0].lower()


def _ticket_terminado(consulta: tuple[str, object] | None) -> bool:
    """La propuesta está lista para descargarse (estado 'terminado')."""
    return consulta is not None and "terminado" in consulta[0].lower()


def _clientes(tipo_libro: TipoLibro):
    """(solicitar, consultar, descargar) del módulo SUNAT según el libro."""
    if tipo_libro == TipoLibro.compras:
        return (
            sunat_compras.solicitar_export_compras,
            sunat_compras.consultar_ticket_compras,
            sunat_compras.descargar_ticket_compras,
        )
    return (
        sunat_ventas.solicitar_export_ventas,
        sunat_ventas.consultar_ticket_ventas,
        sunat_ventas.descargar_ticket_ventas,
    )


def _token_factory(company_id: int, creds: SireCredentialsModel, ruc: str):
    """Closure que entrega (y cachea) el token OAuth de SUNAT para la empresa."""
    snapshot = SimpleNamespace(
        client_id=creds.client_id,
        client_secret_enc=creds.client_secret_enc,
        clave_sol_enc=creds.clave_sol_enc,
        usuario_sol=creds.usuario_sol,
    )

    async def get_token(force_refresh: bool = False) -> str:
        return await get_sunat_token(company_id, snapshot, ruc, force_refresh)

    return get_token


def _descripcion_cobertura(fechas: list[str] | None) -> str | None:
    """Texto legible de la cobertura declarada, para el Excel y trazabilidad."""
    if fechas is None:
        return None
    if not fechas:
        return "Mes completo"

    def fmt(d: str) -> str:
        return f"{d[8:10]}/{d[5:7]}/{d[0:4]}"

    fs = sorted(fechas)
    if len(fs) == 1:
        return fmt(fs[0])
    try:
        from datetime import date

        ds = [date.fromisoformat(f) for f in fs]
        contiguo = all((ds[i + 1] - ds[i]).days == 1 for i in range(len(ds) - 1))
    except ValueError:
        contiguo = False
    if contiguo:
        return f"del {fmt(fs[0])} al {fmt(fs[-1])}"
    if len(fs) <= 6:
        return ", ".join(fmt(f) for f in fs)
    return f"{len(fs)} días entre el {fmt(fs[0])} y el {fmt(fs[-1])}"


async def consultar_propuesta_disponible(
    db: Session, company_id: int, periodo: str, tipo_libro: TipoLibro
) -> dict:
    """
    Indica si existe una propuesta SUNAT fresca (<24h, 'terminada') de otro job
    del mismo periodo/libro que podría reutilizarse en vez de solicitar una nueva.
    """
    no_disponible = {"disponible": False, "generado_a": None}
    repo = SqlReconciliationRepository(db)
    candidato = repo.buscar_ticket_fresco(company_id, periodo, tipo_libro)
    if candidato is None:
        return no_disponible

    company = db.get(Company, company_id)
    creds = db.scalar(
        select(SireCredentialsModel).where(SireCredentialsModel.company_id == company_id)
    )
    if company is None or creds is None:
        return no_disponible

    get_token = _token_factory(company_id, creds, company.ruc)
    _, consultar, _ = _clientes(tipo_libro)
    try:
        consulta = await consultar(get_token, candidato.num_ticket, periodo)
    except Exception:  # una falla de red no debe romper la consulta de disponibilidad
        return no_disponible

    if not _ticket_terminado(consulta):
        return no_disponible
    return {
        "disponible": True,
        "generado_a": candidato.propuesta_origen_at or candidato.created_at,
    }


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
    es_compras = tipo_libro == TipoLibro.compras
    sin_sire = bool(job.sin_sire) and es_compras
    reutilizar = bool(job.reutilizar_propuesta)

    mapeo_config = job.mapeo_config
    saved_mapping = (
        SqlFileMappingRepository(db).get_config(company_id, tipo_libro.value)
        if mapeo_config is None
        else None
    )
    cobertura_fechas = job.cobertura_fechas

    get_token = _token_factory(company_id, creds, ruc)
    solicitar, consultar, descargar = _clientes(tipo_libro)

    sunat_tmp_path: str | None = None
    sunat_extra_paths: dict[str, str] = {}
    meses_no_disponibles: list[str] = []
    try:
        # 1. Ticket principal: reanudar (propio) → reutilizar (otro job) → solicitar.
        num_ticket: str | None = None
        if job.num_ticket and _es_fresco(job.propuesta_origen_at or job.created_at):
            if _ticket_vivo(await consultar(get_token, job.num_ticket, periodo)):
                num_ticket = job.num_ticket
        if num_ticket is None and reutilizar:
            candidato = repo.buscar_ticket_fresco(
                company_id, periodo, tipo_libro, exclude_job_id=job_id
            )
            if candidato is not None and _ticket_terminado(
                await consultar(get_token, candidato.num_ticket, periodo)
            ):
                num_ticket = candidato.num_ticket
                job.num_ticket = num_ticket
                job.propuesta_origen_at = (
                    candidato.propuesta_origen_at or candidato.created_at
                )
                db.commit()
        if num_ticket is None:
            num_ticket = await solicitar(get_token, periodo)
            job.num_ticket = num_ticket
            job.propuesta_origen_at = utcnow()
            db.commit()

        sunat_tmp_path = await descargar(get_token, num_ticket, periodo)

        # 2. Compras "sin SIRE": propuestas de meses anteriores para reubicar
        #    los comprobantes rezagados en el Escenario A.
        if sin_sire:
            sondeo = {
                "empresa_file_path": job.empresa_file_path,
                "empresa_filename": job.empresa_filename or "",
                "tipo_libro": tipo_libro.value,
                "mapeo_config": mapeo_config,
                "saved_mapping": saved_mapping,
                "periodo": periodo,
            }
            meses = await asyncio.to_thread(extraer_periodos_emision, sondeo)
            tickets_previos = dict(job.extra_tickets or {})
            job_fresco = _es_fresco(job.created_at)
            candidatos_reuso: dict[str, str] = (
                repo.buscar_tickets_frescos_multi(
                    company_id, meses, tipo_libro, exclude_job_id=job_id
                )
                if reutilizar
                else {}
            )
            extra_tickets: dict[str, str] = {}

            for mes in meses:
                num_mes: str | None = None
                # Reanudar: ticket que este job ya generó para el mes.
                if job_fresco and mes in tickets_previos:
                    if _ticket_vivo(await consultar(get_token, tickets_previos[mes], mes)):
                        num_mes = tickets_previos[mes]
                # Reutilizar: propuesta fresca de otro job para el mes.
                if num_mes is None and mes in candidatos_reuso:
                    if _ticket_terminado(
                        await consultar(get_token, candidatos_reuso[mes], mes)
                    ):
                        num_mes = candidatos_reuso[mes]
                # Solicitar una nueva.
                if num_mes is None:
                    try:
                        num_mes = await solicitar(get_token, mes)
                    except Exception as exc:  # una propuesta que falla no aborta el job
                        logger.warning(
                            "Job #%s sin_sire: no se pudo solicitar la propuesta de %s (%s)",
                            job_id, mes, exc,
                        )
                        continue
                extra_tickets[mes] = num_mes
                try:
                    sunat_extra_paths[mes] = await descargar(get_token, num_mes, mes)
                except Exception as exc:
                    logger.warning(
                        "Job #%s sin_sire: no se pudo descargar la propuesta de %s (%s)",
                        job_id, mes, exc,
                    )

            meses_no_disponibles = [m for m in meses if m not in sunat_extra_paths]
            if extra_tickets:
                job.extra_tickets = extra_tickets
                db.commit()

        payload = {
            "empresa_file_path": job.empresa_file_path,
            "empresa_filename": job.empresa_filename or "",
            "sunat_tmp_path": sunat_tmp_path,
            "tipo_libro": tipo_libro.value,
            "mapeo_config": mapeo_config,
            "saved_mapping": saved_mapping,
            "cobertura_fechas": cobertura_fechas,
            "cobertura_desc": _descripcion_cobertura(cobertura_fechas),
            "sin_sire": sin_sire,
            "sunat_extra_paths": sunat_extra_paths,
            "sunat_extra_fallidos": meses_no_disponibles,
            "ruc": ruc,
            "empresa_nombre": company.razon_social,
            "periodo": periodo,
            "propuesta_origen_at": job.propuesta_origen_at,
            "company_id": company_id,
            "job_id": job_id,
        }
        # El motor (pandas/openpyxl) es CPU-bound: se corre en un hilo aparte
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
        for ruta in (sunat_tmp_path, *sunat_extra_paths.values()):
            if ruta:
                try:
                    os.remove(ruta)
                except OSError:
                    pass
