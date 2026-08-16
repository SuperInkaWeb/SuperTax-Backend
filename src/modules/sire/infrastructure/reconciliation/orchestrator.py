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
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
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

# El motor de conciliación (pandas/openpyxl) se ejecuta en un subproceso que se
# crea y destruye por cada job: así toda su memoria (DataFrames grandes) vuelve
# al SO al terminar, en vez de acumularse en el worker de larga vida. Se usa
# "spawn" (no "fork") para arrancar un intérprete limpio y evitar deadlocks al
# forkear un proceso con hilos/async.
_MP_CONTEXT = multiprocessing.get_context("spawn")


async def _en_subproceso(func, payload):
    """Corre `func(payload)` (CPU-bound) en un subproceso efímero y devuelve su
    resultado. El subproceso muere al salir del `with`, liberando su memoria."""
    loop = asyncio.get_running_loop()
    with ProcessPoolExecutor(max_workers=1, mp_context=_MP_CONTEXT) as executor:
        return await loop.run_in_executor(executor, func, payload)


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


@dataclass
class _JobSnapshot:
    """Datos del job/empresa leídos a variables planas al inicio del proceso.

    A partir de la lectura inicial NO se vuelve a tocar el ORM `job`/`company`:
    las esperas de SUNAT (~30 min) no mantienen ninguna conexión a la BD, y cada
    escritura es un burst breve. Así una BD serverless (Neon) no corta conexiones
    ociosas a mitad de un job largo — mismo patrón que el proyecto original.
    """
    company_id: int
    ruc: str
    razon_social: str
    periodo: str
    tipo_libro: TipoLibro
    sin_sire: bool
    reutilizar: bool
    mapeo_config: dict | None
    saved_mapping: dict | None
    cobertura_fechas: list | None
    empresa_file_path: str | None
    empresa_filename: str
    num_ticket: str | None
    propuesta_origen_at: datetime | None
    created_at: datetime
    extra_tickets: dict[str, str]


async def procesar_job(db: Session, job_id: int) -> None:
    repo = SqlReconciliationRepository(db)

    # ── Lectura inicial (único momento en que se toca el ORM) ──
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

    tipo_libro = job.tipo_libro
    snap = _JobSnapshot(
        company_id=job.company_id,
        ruc=company.ruc,
        razon_social=company.razon_social,
        periodo=job.periodo,
        tipo_libro=tipo_libro,
        sin_sire=bool(job.sin_sire) and tipo_libro == TipoLibro.compras,
        reutilizar=bool(job.reutilizar_propuesta),
        mapeo_config=job.mapeo_config,
        saved_mapping=(
            SqlFileMappingRepository(db).get_config(job.company_id, tipo_libro.value)
            if job.mapeo_config is None
            else None
        ),
        cobertura_fechas=job.cobertura_fechas,
        empresa_file_path=job.empresa_file_path,
        empresa_filename=job.empresa_filename or "",
        num_ticket=job.num_ticket,
        propuesta_origen_at=job.propuesta_origen_at,
        created_at=job.created_at,
        extra_tickets=dict(job.extra_tickets or {}),
    )
    get_token = _token_factory(snap.company_id, creds, snap.ruc)
    solicitar, consultar, descargar = _clientes(tipo_libro)
    # Cierra la transacción de lectura → libera la conexión antes de las esperas.
    db.commit()

    sunat_tmp_path: str | None = None
    sunat_extra_paths: dict[str, str] = {}
    meses_no_disponibles: list[str] = []
    propuesta_origen_at = snap.propuesta_origen_at
    try:
        # 1. Ticket principal: reanudar (propio) → reutilizar (otro job) → solicitar.
        num_ticket: str | None = None
        if snap.num_ticket and _es_fresco(snap.propuesta_origen_at or snap.created_at):
            if _ticket_vivo(await consultar(get_token, snap.num_ticket, snap.periodo)):
                num_ticket = snap.num_ticket
        if num_ticket is None and snap.reutilizar:
            candidato = repo.buscar_ticket_fresco(
                snap.company_id, snap.periodo, tipo_libro, exclude_job_id=job_id
            )
            # Se extraen sus campos a planos antes de liberar la conexión.
            cand_ticket = candidato.num_ticket if candidato is not None else None
            cand_origen = (
                candidato.propuesta_origen_at or candidato.created_at
                if candidato is not None
                else None
            )
            db.commit()
            if cand_ticket is not None and _ticket_terminado(
                await consultar(get_token, cand_ticket, snap.periodo)
            ):
                num_ticket = cand_ticket
                propuesta_origen_at = cand_origen
                repo.set_ticket_principal(job_id, num_ticket, cand_origen)
        if num_ticket is None:
            num_ticket = await solicitar(get_token, snap.periodo)
            propuesta_origen_at = utcnow()
            repo.set_ticket_principal(job_id, num_ticket, propuesta_origen_at)

        sunat_tmp_path = await descargar(get_token, num_ticket, snap.periodo)

        # 2. Compras "sin SIRE": propuestas de meses anteriores para reubicar
        #    los comprobantes rezagados en el Escenario A.
        if snap.sin_sire:
            sondeo = {
                "empresa_file_path": snap.empresa_file_path,
                "empresa_filename": snap.empresa_filename,
                "tipo_libro": tipo_libro.value,
                "mapeo_config": snap.mapeo_config,
                "saved_mapping": snap.saved_mapping,
                "periodo": snap.periodo,
            }
            meses = await _en_subproceso(extraer_periodos_emision, sondeo)
            tickets_previos = snap.extra_tickets
            job_fresco = _es_fresco(snap.created_at)
            candidatos_reuso: dict[str, str] = (
                repo.buscar_tickets_frescos_multi(
                    snap.company_id, meses, tipo_libro, exclude_job_id=job_id
                )
                if snap.reutilizar
                else {}
            )
            db.commit()  # libera la conexión antes del bucle largo de SUNAT

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
                repo.set_extra_tickets(job_id, extra_tickets)

        payload = {
            "empresa_file_path": snap.empresa_file_path,
            "empresa_filename": snap.empresa_filename,
            "sunat_tmp_path": sunat_tmp_path,
            "tipo_libro": tipo_libro.value,
            "mapeo_config": snap.mapeo_config,
            "saved_mapping": snap.saved_mapping,
            "cobertura_fechas": snap.cobertura_fechas,
            "cobertura_desc": _descripcion_cobertura(snap.cobertura_fechas),
            "sin_sire": snap.sin_sire,
            "sunat_extra_paths": sunat_extra_paths,
            "sunat_extra_fallidos": meses_no_disponibles,
            "ruc": snap.ruc,
            "empresa_nombre": snap.razon_social,
            "periodo": snap.periodo,
            "propuesta_origen_at": propuesta_origen_at,
            "company_id": snap.company_id,
            "job_id": job_id,
        }
        # El motor (pandas/openpyxl) es CPU-bound y pesado en memoria: corre en un
        # subproceso efímero que muere al terminar, liberando su RAM al SO.
        result = await _en_subproceso(procesar_conciliacion, payload)
        repo.save_success(job_id, result)
    except Exception as exc:  # se registra y se refleja como error en el job
        db.rollback()  # descarta cualquier transacción rota antes de reescribir el job
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
