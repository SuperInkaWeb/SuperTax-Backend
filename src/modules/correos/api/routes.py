"""
Endpoints del módulo Correos (montados bajo /api/correos).

Conexiones de buzón (listar, vincular por OAuth Gmail/Outlook, desconectar) y
escaneos (lanzar, listar, ver, cancelar, errores). El progreso de un escaneo se
sigue por polling de GET /scans/{id} o en vivo por SSE en /scans/{id}/stream.
La extracción, la revisión y la exportación llegan después.
"""
import time

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.correos.api.schemas import (
    AuthorizeRequest,
    AuthorizeResponse,
    CallbackRequest,
    ErrorSummary,
    MailboxSummary,
    ScanRequest,
    ScanSummary,
)
from src.modules.correos.application import mailbox_oauth
from src.modules.correos.infrastructure.models import ScanJobModel, ScanStatus
from src.modules.correos.infrastructure.repositories import (
    SqlErrorRepository,
    SqlMailboxRepository,
    SqlScanRepository,
)
from src.modules.correos.infrastructure.scan_dispatch import encolar_escaneo
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import SessionLocal, get_db
from src.platform.identity.auth0 import Auth0Error, validar_token
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
    ctx: ActiveContext = Depends(require_permission("correos.mailbox.create")),
    db: Session = Depends(get_db),
) -> MailboxSummary:
    """Completa la vinculación: canjea el código por tokens y guarda la conexión."""
    try:
        conn = mailbox_oauth.completar(db, ctx.company.id, ctx.user.id, body.code, body.state)
    except mailbox_oauth.OAuthError as exc:
        raise HTTPException(exc.status_code, detail=exc.message) from exc
    return MailboxSummary.from_model(conn)


@router.delete("/mailboxes/{connection_id}", status_code=status.HTTP_204_NO_CONTENT)
def desconectar_buzon(
    connection_id: int,
    ctx: ActiveContext = Depends(require_permission("correos.mailbox.delete")),
    db: Session = Depends(get_db),
) -> None:
    """Desconecta (borra) una conexión de buzón de la empresa activa."""
    if not SqlMailboxRepository(db).delete(connection_id, ctx.company.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conexión no encontrada")


@router.post("/scans", response_model=ScanSummary, status_code=status.HTTP_201_CREATED)
def lanzar_escaneo(
    body: ScanRequest,
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
