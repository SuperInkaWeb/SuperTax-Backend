"""
Endpoints del módulo Correos (montados bajo /api/correos).

Conexiones de buzón (listar, vincular por OAuth Gmail/Outlook, desconectar) y
escaneos (lanzar, listar, ver, cancelar, errores). El progreso de un escaneo se
sigue por polling de GET /scans/{id} o en vivo por SSE en /scans/{id}/stream.
La extracción, la revisión y la exportación llegan después.
"""
import io
import re
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.correos.api.schemas import (
    AuditEntry,
    AuditListResponse,
    AuthorizeRequest,
    AuthorizeResponse,
    CallbackRequest,
    CorreccionRequest,
    ErrorSummary,
    MailboxSummary,
    RecordListResponse,
    RecordSummary,
    ScanRequest,
    ScanSummary,
)
from src.modules.correos.application import (
    audit_service,
    mailbox_oauth,
    record_service,
    report_service,
)
from src.modules.correos.application.record_service import CorreccionInvalida, FiltrosRegistro
from src.modules.correos.domain.audit import AuditAction
from src.modules.correos.infrastructure.models import ScanJobModel, ScanStatus
from src.modules.correos.infrastructure.repositories import (
    SqlAttachmentRepository,
    SqlErrorRepository,
    SqlMailboxRepository,
    SqlScanRepository,
)
from src.modules.correos.infrastructure.scan_dispatch import encolar_escaneo
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import SessionLocal, get_db
from src.platform.identity.auth0 import Auth0Error, validar_token
from src.platform.storage import FileStorage, get_storage
from src.platform.tenancy.current_tenant import ActiveContext
from src.platform.users.models import User

_ESTADOS_FINALES = (
    ScanStatus.completed.value,
    ScanStatus.failed.value,
    ScanStatus.cancelled.value,
)

router = APIRouter(tags=["correos"], dependencies=[Depends(require_module("correos"))])


@router.get("/mailboxes", response_model=list[MailboxSummary])
def listar_buzones(
    ctx: ActiveContext = Depends(require_permission("correos.mailbox.read")),
    db: Session = Depends(get_db),
) -> list[MailboxSummary]:
    """Conexiones de buzón de la empresa activa, de la más reciente a la más antigua."""
    buzones = SqlMailboxRepository(db).list_by_company(ctx.company.id)
    return [MailboxSummary.from_model(b) for b in buzones]


@router.post("/mailboxes/authorize", response_model=AuthorizeResponse)
def autorizar_buzon(
    body: AuthorizeRequest,
    ctx: ActiveContext = Depends(require_permission("correos.mailbox.create")),
) -> AuthorizeResponse:
    """Devuelve la URL de consentimiento OAuth. El frontend redirige al proveedor."""
    try:
        url = mailbox_oauth.autorizar(ctx.company.id, ctx.user.id, body.provider)
    except mailbox_oauth.OAuthError as exc:
        raise HTTPException(exc.status_code, detail=exc.message) from exc
    return AuthorizeResponse(authorization_url=url)


@router.post(
    "/mailboxes/callback", response_model=MailboxSummary, status_code=status.HTTP_201_CREATED
)
def callback_buzon(
    body: CallbackRequest,
    request: Request,
    ctx: ActiveContext = Depends(require_permission("correos.mailbox.create")),
    db: Session = Depends(get_db),
) -> MailboxSummary:
    """Completa la vinculación: canjea el código por tokens y guarda la conexión."""
    try:
        conn = mailbox_oauth.completar(db, ctx.company.id, ctx.user.id, body.code, body.state)
    except mailbox_oauth.OAuthError as exc:
        raise HTTPException(exc.status_code, detail=exc.message) from exc
    audit_service.registrar(
        db, company_id=ctx.company.id, actor_id=ctx.user.id,
        action=AuditAction.BUZON_VINCULADO, resource_type="mailbox_connection",
        resource_id=conn.id, actor_ip=_ip(request),
        metadata={"provider": conn.provider, "account_email": conn.account_email},
    )
    return MailboxSummary.from_model(conn)


@router.delete("/mailboxes/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def desconectar_buzon(
    connection_id: int,
    request: Request,
    ctx: ActiveContext = Depends(require_permission("correos.mailbox.delete")),
    db: Session = Depends(get_db),
) -> None:
    """Desconecta (borra) una conexión de buzón de la empresa activa."""
    if not SqlMailboxRepository(db).delete(connection_id, ctx.company.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conexión no encontrada")
    audit_service.registrar(
        db, company_id=ctx.company.id, actor_id=ctx.user.id,
        action=AuditAction.BUZON_DESVINCULADO, resource_type="mailbox_connection",
        resource_id=connection_id, actor_ip=_ip(request),
    )


@router.post("/scans", response_model=ScanSummary, status_code=status.HTTP_201_CREATED)
def lanzar_escaneo(
    body: ScanRequest,
    request: Request,
    ctx: ActiveContext = Depends(require_permission("correos.scan.create")),
    db: Session = Depends(get_db),
) -> ScanSummary:
    """Crea un escaneo y lo encola (el frontend consulta GET /scans/{id} hasta terminar)."""
    if SqlMailboxRepository(db).get(body.connection_id, ctx.company.id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conexión de buzón no encontrada")
    params = {
        "desde": body.desde.isoformat() if body.desde else None,
        "hasta": body.hasta.isoformat() if body.hasta else None,
        "limite_de_mensajes": body.limite_de_mensajes,
        "carpeta": body.carpeta,
    }
    job = SqlScanRepository(db).create(
        company_id=ctx.company.id, requested_by_id=ctx.user.id,
        connection_id=body.connection_id, params=params,
    )
    audit_service.registrar(
        db, company_id=ctx.company.id, actor_id=ctx.user.id,
        action=AuditAction.ESCANEO_INICIADO, resource_type="scan_job",
        resource_id=job.id, actor_ip=_ip(request),
        metadata={"connection_id": body.connection_id, "limite": body.limite_de_mensajes},
    )
    encolar_escaneo(job.id)
    return ScanSummary.from_model(job)


@router.get("/scans", response_model=list[ScanSummary])
def listar_escaneos(
    ctx: ActiveContext = Depends(require_permission("correos.scan.read")),
    db: Session = Depends(get_db),
) -> list[ScanSummary]:
    """Escaneos de la empresa activa, del más reciente al más antiguo."""
    jobs = SqlScanRepository(db).list_by_company(ctx.company.id)
    return [ScanSummary.from_model(j) for j in jobs]


@router.get("/scans/{job_id}", response_model=ScanSummary)
def ver_escaneo(
    job_id: int,
    ctx: ActiveContext = Depends(require_permission("correos.scan.read")),
    db: Session = Depends(get_db),
) -> ScanSummary:
    """Estado y progreso de un escaneo."""
    job = SqlScanRepository(db).get(job_id, ctx.company.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Escaneo no encontrado")
    return ScanSummary.from_model(job)


@router.post("/scans/{job_id}/cancel", response_model=ScanSummary)
def cancelar_escaneo(
    job_id: int,
    request: Request,
    ctx: ActiveContext = Depends(require_permission("correos.scan.create")),
    db: Session = Depends(get_db),
) -> ScanSummary:
    """Pide cancelar un escaneo en cola o en curso (se detiene en el próximo correo)."""
    repo = SqlScanRepository(db)
    if not repo.request_cancel(job_id, ctx.company.id):
        job = repo.get(job_id, ctx.company.id)
        if job is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Escaneo no encontrado")
        raise HTTPException(status.HTTP_409_CONFLICT, detail="El escaneo ya terminó.")
    audit_service.registrar(
        db, company_id=ctx.company.id, actor_id=ctx.user.id,
        action=AuditAction.ESCANEO_CANCELADO, resource_type="scan_job",
        resource_id=job_id, actor_ip=_ip(request),
    )
    return ScanSummary.from_model(repo.get(job_id, ctx.company.id))


@router.get("/scans/{job_id}/errors", response_model=list[ErrorSummary])
def errores_de_escaneo(
    job_id: int,
    ctx: ActiveContext = Depends(require_permission("correos.scan.read")),
    db: Session = Depends(get_db),
) -> list[ErrorSummary]:
    """Errores de procesamiento de un escaneo (adjuntos rechazados, fallos del buzón…)."""
    if SqlScanRepository(db).get(job_id, ctx.company.id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Escaneo no encontrado")
    errores = SqlErrorRepository(db).list_by_job(job_id, ctx.company.id)
    return [ErrorSummary.from_model(e) for e in errores]


@router.get("/scans/{job_id}/stream")
def stream_escaneo(job_id: int, token: str = "", db: Session = Depends(get_db)):
    """
    Progreso del escaneo en vivo por SSE (alternativa al polling de GET /scans/{id}).

    Auth manual: EventSource no envía cabeceras, así que el token Auth0 llega por
    query param y se comprueba que el solicitante sea quien lanzó el escaneo. El
    chequeo usa la sesión del request; el streaming abre sesiones cortas propias
    para no retener una conexión en transacción durante minutos.
    """
    job = db.scalar(select(ScanJobModel).where(ScanJobModel.id == job_id))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Escaneo no encontrado")
    try:
        claims = validar_token(token)
    except Auth0Error as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="No autorizado") from exc
    user = db.scalar(select(User).where(User.auth0_sub == claims.get("sub")))
    if user is None or job.requested_by_id != user.id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="No autorizado")
    company_id = job.company_id
    db.rollback()

    def stream():
        ultimo = ""
        while True:
            sesion = SessionLocal()
            try:
                fila = SqlScanRepository(sesion).get(job_id, company_id)
                if fila is None:
                    yield "data: __FIN__\n\n"
                    return
                payload = ScanSummary.from_model(fila).model_dump_json()
                estado = fila.status
            finally:
                sesion.close()
            if payload != ultimo:
                ultimo = payload
                yield f"event: progress\ndata: {payload}\n\n"
            if estado in _ESTADOS_FINALES:
                yield "data: __FIN__\n\n"
                return
            yield ": heartbeat\n\n"
            time.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── Registros extraídos y revisión humana ────────────────────────────

@router.get("/records", response_model=RecordListResponse)
def listar_registros(
    ctx: ActiveContext = Depends(require_permission("correos.record.read")),
    db: Session = Depends(get_db),
    job_id: int | None = None,
    ruc: str | None = Query(default=None, max_length=11, pattern=r"^\d{11}$"),
    ruc_inquilino: str | None = Query(default=None, max_length=11, pattern=r"^\d{11}$"),
    periodo: str | None = Query(default=None, max_length=6, pattern=r"^\d{6}$"),
    periodo_desde: str | None = Query(default=None, max_length=6, pattern=r"^\d{6}$"),
    periodo_hasta: str | None = Query(default=None, max_length=6, pattern=r"^\d{6}$"),
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    solo_aprobados: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> RecordListResponse:
    """Listado de registros de la empresa activa, filtrable y paginado."""
    filtros = FiltrosRegistro(
        job_id=job_id, ruc=ruc, ruc_inquilino=ruc_inquilino, periodo=periodo,
        periodo_desde=periodo_desde, periodo_hasta=periodo_hasta,
        fecha_desde=fecha_desde, fecha_hasta=fecha_hasta, solo_aprobados=solo_aprobados,
    )
    return _lista(db, ctx.company.id, filtros, limit, offset)


_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@router.get("/records/export")
def exportar_registros(
    request: Request,
    ctx: ActiveContext = Depends(require_permission("correos.export.create")),
    db: Session = Depends(get_db),
    job_id: int | None = None,
    ruc: str | None = Query(default=None, max_length=11, pattern=r"^\d{11}$"),
    ruc_inquilino: str | None = Query(default=None, max_length=11, pattern=r"^\d{11}$"),
    periodo: str | None = Query(default=None, max_length=6, pattern=r"^\d{6}$"),
    periodo_desde: str | None = Query(default=None, max_length=6, pattern=r"^\d{6}$"),
    periodo_hasta: str | None = Query(default=None, max_length=6, pattern=r"^\d{6}$"),
    fecha_desde: date | None = None,
    fecha_hasta: date | None = None,
    solo_aprobados: bool = False,
) -> StreamingResponse:
    """Descarga el reporte Excel con los registros que cumplen el filtro (mismos filtros
    que GET /records). Se genera al vuelo; si el total supera el tope se exportan los
    más recientes. La cabecera X-Total-Registros informa cuántos cumplían el filtro."""
    filtros = FiltrosRegistro(
        job_id=job_id, ruc=ruc, ruc_inquilino=ruc_inquilino, periodo=periodo,
        periodo_desde=periodo_desde, periodo_hasta=periodo_hasta,
        fecha_desde=fecha_desde, fecha_hasta=fecha_hasta, solo_aprobados=solo_aprobados,
    )
    xlsx, exportadas, total = report_service.generar_excel(db, ctx.company.id, filtros)
    audit_service.registrar(
        db, company_id=ctx.company.id, actor_id=ctx.user.id,
        action=AuditAction.REPORTE_EXPORTADO, resource_type="report",
        actor_ip=_ip(request), metadata={"filas_exportadas": exportadas, "total": total},
    )
    nombre = f"reporte-correos-{date.today():%Y%m%d}.xlsx"
    return StreamingResponse(
        io.BytesIO(xlsx), media_type=_MIME_XLSX,
        headers={
            "Content-Disposition": f'attachment; filename="{nombre}"',
            "X-Total-Registros": str(total),
            "X-Filas-Exportadas": str(exportadas),
        },
    )


@router.get("/review", response_model=RecordListResponse)
def cola_de_revision(
    ctx: ActiveContext = Depends(require_permission("correos.record.read")),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> RecordListResponse:
    """Registros que el pipeline no pudo dar por buenos (cola de revisión)."""
    return _lista(db, ctx.company.id, FiltrosRegistro(solo_pendientes=True), limit, offset)


@router.get("/review/count", response_model=dict)
def pendientes_de_revision(
    ctx: ActiveContext = Depends(require_permission("correos.record.read")),
    db: Session = Depends(get_db),
) -> dict:
    """Contador de pendientes para el aviso de la interfaz."""
    return {"pendientes": record_service.contar_pendientes(db, ctx.company.id)}


@router.get("/records/{record_id}", response_model=RecordSummary)
def ver_registro(
    record_id: int,
    ctx: ActiveContext = Depends(require_permission("correos.record.read")),
    db: Session = Depends(get_db),
) -> RecordSummary:
    """Un registro con su procedencia."""
    registro, origen = _obtener(db, ctx.company.id, record_id)
    return RecordSummary.from_model(registro, origen)


@router.get("/records/{record_id}/document")
def ver_documento(
    record_id: int,
    request: Request,
    ctx: ActiveContext = Depends(require_permission("correos.record.read")),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> Response:
    """Sirve el adjunto original (autenticado). Se comprueba antes que el registro es
    de la empresa, así el id de adjunto no sirve para sondear documentos ajenos."""
    registro, _ = _obtener(db, ctx.company.id, record_id)
    adjunto = SqlAttachmentRepository(db).get(registro.attachment_id, ctx.company.id)
    if adjunto is None or not storage.exists(adjunto.storage_key):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Documento no disponible")
    contenido = storage.read(adjunto.storage_key)
    audit_service.registrar(
        db, company_id=ctx.company.id, actor_id=ctx.user.id,
        action=AuditAction.ADJUNTO_DESCARGADO, resource_type="attachment",
        resource_id=adjunto.id, actor_ip=_ip(request),
    )
    disposicion = f'inline; filename="{_nombre_seguro(adjunto.original_name)}"'
    return Response(
        content=contenido, media_type=adjunto.mime_type,
        headers={"Content-Disposition": disposicion},
    )


@router.patch("/records/{record_id}", response_model=RecordSummary)
def corregir_registro(
    record_id: int,
    body: CorreccionRequest,
    request: Request,
    ctx: ActiveContext = Depends(require_permission("correos.record.update")),
    db: Session = Depends(get_db),
) -> RecordSummary:
    """Aplica las correcciones de una persona y aprueba el registro. Los valores pasan
    por los mismos objetos de valor que la extracción (un humano también teclea mal)."""
    try:
        registro = record_service.corregir_y_aprobar(
            db, ctx.company.id, ctx.user.id, record_id, body.correcciones
        )
    except CorreccionInvalida as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.message) from exc
    if registro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Registro no encontrado")
    audit_service.registrar(
        db, company_id=ctx.company.id, actor_id=ctx.user.id,
        action=AuditAction.REGISTRO_CORREGIDO, resource_type="extracted_record",
        resource_id=record_id, actor_ip=_ip(request),
        metadata={"campos": sorted(body.correcciones)},
    )
    return RecordSummary.from_model(registro)


@router.post("/records/{record_id}/approve", response_model=RecordSummary)
def aprobar_registro(
    record_id: int,
    request: Request,
    ctx: ActiveContext = Depends(require_permission("correos.record.update")),
    db: Session = Depends(get_db),
) -> RecordSummary:
    """El revisor confirma que lo extraído es correcto."""
    registro = record_service.aprobar(db, ctx.company.id, ctx.user.id, record_id)
    if registro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Registro no encontrado")
    audit_service.registrar(
        db, company_id=ctx.company.id, actor_id=ctx.user.id,
        action=AuditAction.REGISTRO_APROBADO, resource_type="extracted_record",
        resource_id=record_id, actor_ip=_ip(request),
    )
    return RecordSummary.from_model(registro)


@router.post("/records/{record_id}/reject", response_model=RecordSummary)
def rechazar_registro(
    record_id: int,
    request: Request,
    ctx: ActiveContext = Depends(require_permission("correos.record.update")),
    db: Session = Depends(get_db),
) -> RecordSummary:
    """Marca el registro como no utilizable (documento ilegible o ajeno)."""
    registro = record_service.rechazar(db, ctx.company.id, ctx.user.id, record_id)
    if registro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Registro no encontrado")
    audit_service.registrar(
        db, company_id=ctx.company.id, actor_id=ctx.user.id,
        action=AuditAction.REGISTRO_RECHAZADO, resource_type="extracted_record",
        resource_id=record_id, actor_ip=_ip(request),
    )
    return RecordSummary.from_model(registro)


@router.delete("/records/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_registro(
    record_id: int,
    request: Request,
    ctx: ActiveContext = Depends(require_permission("correos.record.update")),
    db: Session = Depends(get_db),
) -> None:
    """Borra un registro extraído (el adjunto en R2 se conserva, pertenece al escaneo)."""
    if not record_service.eliminar(db, ctx.company.id, record_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Registro no encontrado")
    audit_service.registrar(
        db, company_id=ctx.company.id, actor_id=ctx.user.id,
        action=AuditAction.REGISTRO_ELIMINADO, resource_type="extracted_record",
        resource_id=record_id, actor_ip=_ip(request),
    )


# ── Bitácora de auditoría ────────────────────────────────────────────

@router.get("/audit", response_model=AuditListResponse)
def listar_auditoria(
    ctx: ActiveContext = Depends(require_permission("correos.audit.view")),
    db: Session = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> AuditListResponse:
    """Bitácora de acciones sensibles de la empresa (append-only). Solo admin/supervisor."""
    items, total = audit_service.listar(db, ctx.company.id, limit=limit, offset=offset)
    return AuditListResponse(
        items=[AuditEntry.from_model(e) for e in items],
        total=total, limit=limit, offset=offset,
    )


def _lista(
    db: Session, company_id: int, filtros: FiltrosRegistro, limit: int, offset: int
) -> RecordListResponse:
    items, total, origenes = record_service.listar(db, company_id, filtros, limit=limit,
                                                    offset=offset)
    return RecordListResponse(
        items=[RecordSummary.from_model(r, origenes.get(r.attachment_id)) for r in items],
        total=total, limit=limit, offset=offset,
    )


def _obtener(db: Session, company_id: int, record_id: int):
    resultado = record_service.obtener(db, company_id, record_id)
    if resultado is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Registro no encontrado")
    return resultado


def _nombre_seguro(nombre: str) -> str:
    """Sanea el nombre para la cabecera Content-Disposition (evita inyección de cabecera)."""
    limpio = re.sub(r'[^\w.\- ]', "", nombre or "").strip()
    return limpio[:120] or "documento"


def _ip(request: Request) -> str | None:
    """IP del cliente para la auditoría (informativa). El manejo de proxy es del
    middleware de la plataforma; aquí solo se lee el peer de la conexión."""
    return request.client.host if request.client else None
