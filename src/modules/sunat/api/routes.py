"""
Endpoints del módulo SUNAT (montados bajo /api/sunat).

Los endpoints normales pasan por `require_module("sunat")` + `require_permission`
y se filtran por empresa activa. El streaming de logs (`/logs`) es SSE: el
navegador (EventSource) no puede enviar cabeceras, así que valida el token Auth0
por query param y comprueba la propiedad del job manualmente.
"""
import io
import json
import time

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.sunat.api.schemas import (
    CredentialsInput,
    CredentialsStatusResponse,
    DriveStatusResponse,
    JobResultDetailResponse,
    SunatJobListItem,
)
from src.modules.sunat.application import drive_service, job_service
from src.modules.sunat.application.credentials import (
    get_credentials_status,
    set_credentials,
)
from src.modules.sunat.infrastructure.models import SunatJobStatus
from src.modules.sunat.infrastructure.report_excel import generar_reporte
from src.modules.sunat.infrastructure.repositories import (
    SqlDriveTokenRepository,
    SqlJobResultRepository,
    SqlSunatCredentialsRepository,
    SqlSunatJobRepository,
)
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import SessionLocal, get_db
from src.platform.identity.auth0 import Auth0Error, validar_token
from src.platform.storage import get_storage
from src.platform.storage.base import FileStorage
from src.platform.tenancy.current_tenant import ActiveContext
from src.platform.users.models import User

router = APIRouter(tags=["sunat"])

_MODULO = [Depends(require_module("sunat"))]


def _b(valor: str) -> bool:
    return valor.lower() == "true"


# ─────────────────────── Consulta / configuración ───────────────────────
@router.get("/jobs", response_model=list[SunatJobListItem], dependencies=_MODULO)
def list_jobs(
    limit: int = 100,
    offset: int = 0,
    ctx: ActiveContext = Depends(require_permission("sunat.job.read")),
    db: Session = Depends(get_db),
) -> list[SunatJobListItem]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    jobs = SqlSunatJobRepository(db).list_by_company(ctx.company.id, limit, offset)
    con_resultado = SqlJobResultRepository(db).job_ids_con_resultado([j.job_id for j in jobs])
    return [
        SunatJobListItem(
            job_id=j.job_id,
            status=j.status,
            created_at=j.created_at,
            completed_at=j.completed_at,
            has_result=j.job_id in con_resultado,
        )
        for j in jobs
    ]


@router.get("/jobs/{job_id}", response_model=JobResultDetailResponse, dependencies=_MODULO)
def get_job(
    job_id: str,
    ctx: ActiveContext = Depends(require_permission("sunat.job.read")),
    db: Session = Depends(get_db),
) -> JobResultDetailResponse:
    fila = SqlJobResultRepository(db).get_by_job_id(job_id, ctx.company.id)
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Resultado no encontrado")
    return JobResultDetailResponse(
        job_id=fila.job_id,
        created_at=fila.created_at,
        resultados=json.loads(fila.resultados),
    )


@router.get("/jobs/{job_id}/report", dependencies=_MODULO)
def descargar_reporte(
    job_id: str,
    ctx: ActiveContext = Depends(require_permission("sunat.job.read")),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Descarga el reporte Excel del job: estado por comprobante + descripción."""
    fila = SqlJobResultRepository(db).get_by_job_id(job_id, ctx.company.id)
    if fila is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Resultado no encontrado")
    xlsx = generar_reporte(json.loads(fila.resultados))
    return StreamingResponse(
        io.BytesIO(xlsx),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="reporte-sunat-{job_id}.xlsx"'},
    )


@router.get("/credentials", response_model=CredentialsStatusResponse, dependencies=_MODULO)
def credentials_status(
    ctx: ActiveContext = Depends(require_permission("sunat.credentials.manage")),
    db: Session = Depends(get_db),
) -> CredentialsStatusResponse:
    estado = get_credentials_status(SqlSunatCredentialsRepository(db), ctx.company.id)
    return CredentialsStatusResponse.model_validate(estado)


@router.put("/credentials", response_model=CredentialsStatusResponse, dependencies=_MODULO)
def upsert_credentials(
    payload: CredentialsInput,
    ctx: ActiveContext = Depends(require_permission("sunat.credentials.manage")),
    db: Session = Depends(get_db),
) -> CredentialsStatusResponse:
    repo = SqlSunatCredentialsRepository(db)
    try:
        set_credentials(
            repo,
            company_id=ctx.company.id,
            user_id=ctx.user.id,
            ruc=payload.ruc,
            usuario=payload.usuario,
            clave=payload.clave,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return CredentialsStatusResponse.model_validate(
        get_credentials_status(repo, ctx.company.id)
    )


@router.get("/drive", response_model=DriveStatusResponse, dependencies=_MODULO)
def drive_status(
    ctx: ActiveContext = Depends(require_permission("sunat.drive.manage")),
    db: Session = Depends(get_db),
) -> DriveStatusResponse:
    token = SqlDriveTokenRepository(db).get(ctx.user.id)
    return DriveStatusResponse(connected=token is not None)


@router.get("/drive/auth", dependencies=_MODULO)
def drive_auth(
    ctx: ActiveContext = Depends(require_permission("sunat.drive.manage")),
) -> dict:
    """Devuelve la URL de autorización de Google Drive para abrir en un popup."""
    return {"url": drive_service.url_autorizacion(ctx.company.id, ctx.user.id)}


def _html_popup(texto: str, code: int = 200, conectado: bool = False) -> HTMLResponse:
    script = (
        "<script>if(window.opener){window.opener.postMessage("
        "{type:'DRIVE_CONNECTED'},'*');}setTimeout(function(){window.close();},1500);"
        "</script>"
        if conectado
        else ""
    )
    return HTMLResponse(
        f"<!doctype html><html><body>"
        f"<p style='font-family:Arial;text-align:center;margin-top:40px'>{texto}</p>"
        f"{script}</body></html>",
        status_code=code,
    )


@router.get("/drive/callback")
def drive_callback(
    code: str, state: str = "", db: Session = Depends(get_db)
) -> HTMLResponse:
    """Callback de Google (popup): valida el state e intercambia el code por tokens."""
    try:
        drive_service.procesar_callback(db, code, state)
    except drive_service.DriveError as exc:
        return _html_popup(
            f"{exc.message}. Cierra esta ventana e intenta de nuevo.", code=exc.status_code
        )
    return _html_popup("Conectado correctamente. Puedes cerrar esta ventana.", conectado=True)


@router.post("/drive/desconectar", dependencies=_MODULO)
def drive_desconectar(
    ctx: ActiveContext = Depends(require_permission("sunat.drive.manage")),
    db: Session = Depends(get_db),
) -> dict:
    drive_service.desconectar(db, ctx.user.id)
    return {"message": "Drive desconectado"}


# ─────────────────────── Ejecución de descargas ───────────────────────
@router.post("/preview-excel", dependencies=_MODULO)
async def preview_excel(
    mapeo: str = Form(""),
    excel: UploadFile | None = File(None),
    ctx: ActiveContext = Depends(require_permission("sunat.job.create")),
    db: Session = Depends(get_db),
) -> dict:
    mapeo_manual = None
    if mapeo.strip():
        try:
            mapeo_manual = json.loads(mapeo)
        except (ValueError, TypeError):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="mapeo no es JSON válido")
    try:
        return await job_service.previsualizar(
            db, ctx.company.id, excel=excel, mapeo_manual=mapeo_manual
        )
    except job_service.SunatJobError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/iniciar", dependencies=_MODULO)
async def iniciar(
    ruc: str = Form(...),
    usuario: str = Form(...),
    clave: str = Form(...),
    usar_correo: str = Form("false"),
    gmail_user: str = Form(""),
    gmail_pass: str = Form(""),
    destino: str = Form(""),
    modo_correo: str = Form("individual"),
    usar_drive: str = Form("false"),
    descargar_pdf: str = Form("true"),
    descargar_xml: str = Form("true"),
    comprobantes_ids: str = Form(""),
    preview_id: str = Form(""),
    excel: UploadFile | None = File(None),
    ctx: ActiveContext = Depends(require_permission("sunat.job.create")),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> dict:
    try:
        job_id = await job_service.iniciar(
            db,
            storage,
            ctx.company.id,
            ctx.user.id,
            ruc=ruc,
            usuario=usuario,
            clave=clave,
            usar_correo=_b(usar_correo),
            gmail_user=gmail_user,
            gmail_pass=gmail_pass,
            destino=destino,
            modo_correo=modo_correo,
            usar_drive=_b(usar_drive),
            descargar_pdf=_b(descargar_pdf),
            descargar_xml=_b(descargar_xml),
            comprobantes_seleccionados=(
                json.loads(comprobantes_ids) if comprobantes_ids.strip() else []
            ),
            preview_id=preview_id,
            excel=excel,
        )
    except job_service.SunatJobError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"job_id": job_id}


@router.post("/forzar-faltantes", dependencies=_MODULO)
async def forzar_faltantes(
    ruc: str = Form(...),
    usuario: str = Form(...),
    clave: str = Form(...),
    usar_correo: str = Form("false"),
    gmail_user: str = Form(""),
    gmail_pass: str = Form(""),
    destino: str = Form(""),
    modo_correo: str = Form("individual"),
    usar_drive: str = Form("false"),
    resultados_previos: str = Form(...),
    excel: UploadFile | None = File(None),
    ctx: ActiveContext = Depends(require_permission("sunat.job.create")),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> dict:
    try:
        job_id = await job_service.forzar_faltantes(
            db,
            storage,
            ctx.company.id,
            ctx.user.id,
            ruc=ruc,
            usuario=usuario,
            clave=clave,
            usar_correo=_b(usar_correo),
            gmail_user=gmail_user,
            gmail_pass=gmail_pass,
            destino=destino,
            modo_correo=modo_correo,
            usar_drive=_b(usar_drive),
            resultados_previos=resultados_previos,
            excel=excel,
        )
    except job_service.SunatJobError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"job_id": job_id}


@router.post("/cancelar/{job_id}", dependencies=_MODULO)
def cancelar(
    job_id: str,
    ctx: ActiveContext = Depends(require_permission("sunat.job.create")),
    db: Session = Depends(get_db),
) -> dict:
    SqlSunatJobRepository(db).request_cancel(job_id, ctx.company.id)
    return {"message": "Cancelación solicitada"}


_ESTADOS_FINALES = (
    SunatJobStatus.completado,
    SunatJobStatus.error,
    SunatJobStatus.cancelado,
)


@router.get("/logs/{job_id}")
def logs(job_id: str, token: str = "", db: Session = Depends(get_db)):
    """
    Stream SSE de logs/progreso leídos de Postgres (el worker los escribe ahí).
    Auth manual: EventSource no envía cabeceras, el token Auth0 llega por query
    param y se valida la propiedad del job (su creador). El chequeo usa la sesión
    del request; el streaming abre sesiones cortas propias.
    """
    job = SqlSunatJobRepository(db).get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job no encontrado")
    try:
        claims = validar_token(token)
    except Auth0Error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="No autorizado")
    user = db.scalar(select(User).where(User.auth0_sub == claims.get("sub")))
    if user is None or job.created_by_id != user.id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="No autorizado")
    # Libera la transacción del request: el streaming (abajo) usa sesiones cortas
    # propias y no debe retener esta conexión en transacción durante minutos.
    db.rollback()

    def stream():
        ultimo_id = 0
        while True:
            db = SessionLocal()
            try:
                repo = SqlSunatJobRepository(db)
                filas = repo.logs_after(job_id, ultimo_id)
                fila_job = repo.get(job_id)
                estado = fila_job.status if fila_job else None
            finally:
                db.close()
            for fila in filas:
                ultimo_id = fila.id
                if fila.kind == "progress":
                    yield f"event: progress\ndata: {fila.message}\n\n"
                else:
                    yield f"id: {fila.id}\ndata: {fila.message}\n\n"
            if not filas:
                if estado in _ESTADOS_FINALES:
                    yield "data: __FIN__\n\n"
                    break
                yield ": heartbeat\n\n"
                time.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream")
