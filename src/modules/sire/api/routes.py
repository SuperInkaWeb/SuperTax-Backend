"""
Endpoints del módulo SIRE (montados bajo /api/sire).

Doble puerta de seguridad:
- `require_module("sire")` a nivel de router: la empresa debe tener el módulo.
- `require_permission(...)` por endpoint: el rol debe poder.
Todo se filtra por la empresa activa (`ctx.company.id`) → aislamiento multi-tenant.
"""
import json

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from src.modules.sire.api.schemas import (
    CredentialsInput,
    CredentialsStatusResponse,
    JobResponse,
)
from src.modules.sire.application import file_mapping
from src.modules.sire.application.credentials import (
    get_credentials_status,
    set_credentials,
)
from src.modules.sire.application.use_cases import (
    create_reconciliation_job,
    get_reconciliation_job,
    list_reconciliation_jobs,
)
from src.modules.sire.domain.entities import TipoLibro
from src.modules.sire.infrastructure.reconciliation.orchestrator import (
    consultar_propuesta_disponible,
)
from src.modules.sire.infrastructure.repositories import (
    SqlCredentialsRepository,
    SqlFileMappingRepository,
    SqlReconciliationRepository,
)
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import get_db
from src.platform.storage import get_storage
from src.platform.storage.base import FileStorage
from src.platform.tenancy.current_tenant import ActiveContext
from src.platform.web.rate_limit import SlidingWindowLimiter

router = APIRouter(tags=["sire"], dependencies=[Depends(require_module("sire"))])

# Cada conciliación dispara descargas costosas a SUNAT: se limita la tasa por
# usuario para evitar abuso de recursos (OWASP: Insecure Design).
_limite_conciliacion = SlidingWindowLimiter(max_attempts=10, window_seconds=60)


def _chequear_limite_conciliacion(user_id: int) -> None:
    espera = _limite_conciliacion.blocked_for(f"user:{user_id}")
    if espera:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiadas conciliaciones en poco tiempo. Espera {espera} segundo(s).",
        )
    _limite_conciliacion.register(f"user:{user_id}")


def _parse_json(raw: str, campo: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail=f"{campo} no es un JSON válido"
        )


def _validar_libro(tipo_libro: str) -> None:
    if tipo_libro not in file_mapping.LIBROS_VALIDOS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="tipo_libro inválido")


# ─────────────────────────── Conciliaciones ───────────────────────────
@router.get("/jobs", response_model=list[JobResponse])
def list_jobs(
    limit: int = 100,
    offset: int = 0,
    ctx: ActiveContext = Depends(require_permission("sire.job.read")),
    db: Session = Depends(get_db),
) -> list[JobResponse]:
    repo = SqlReconciliationRepository(db)
    jobs = list_reconciliation_jobs(repo, ctx.company.id, limit, offset)
    return [JobResponse.model_validate(job) for job in jobs]


@router.post("/jobs", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    periodo: str = Form(..., description="Periodo AAAAMM, ej. 202601"),
    tipo_libro: TipoLibro = Form(...),
    archivo: UploadFile = File(..., description="Archivo TXT/CSV de la empresa"),
    mapeo_columnas: str | None = Form(None, description="JSON del mapeo de columnas"),
    guardar_formato: bool = Form(False, description="Guardar el mapeo como formato de la empresa"),
    cobertura_fechas: str | None = Form(None, description="JSON de fechas (solo ventas)"),
    sin_sire: bool = Form(False, description="Solo compras: empresa no afiliada al SIRE"),
    reutilizar_propuesta: bool = Form(False, description="Reutilizar propuesta fresca si existe"),
    ctx: ActiveContext = Depends(require_permission("sire.job.create")),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> JobResponse:
    _chequear_limite_conciliacion(ctx.user.id)
    content = await archivo.read()
    mapeo_config = _parse_json(mapeo_columnas, "mapeo_columnas") if mapeo_columnas else None

    cobertura: list | None = None
    if cobertura_fechas is not None and tipo_libro == TipoLibro.ventas:
        cobertura = _parse_json(cobertura_fechas, "cobertura_fechas")
        if not isinstance(cobertura, list) or not all(
            isinstance(f, str) and len(f) == 10 for f in cobertura
        ):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="cobertura_fechas debe ser una lista de fechas AAAA-MM-DD",
            )

    try:
        job = create_reconciliation_job(
            SqlReconciliationRepository(db),
            storage,
            company_id=ctx.company.id,
            user_id=ctx.user.id,
            periodo=periodo,
            tipo_libro=tipo_libro,
            filename=archivo.filename,
            content=content,
            sin_sire=sin_sire,
            mapeo_config=mapeo_config,
            cobertura_fechas=cobertura,
            reutilizar_propuesta=reutilizar_propuesta,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))

    if guardar_formato and mapeo_config and mapeo_config.get("columnas"):
        SqlFileMappingRepository(db).save(ctx.company.id, tipo_libro.value, mapeo_config)

    return JobResponse.model_validate(job)


@router.get("/propuesta-disponible")
async def propuesta_disponible(
    periodo: str,
    tipo_libro: TipoLibro,
    ctx: ActiveContext = Depends(require_permission("sire.job.read")),
    db: Session = Depends(get_db),
) -> dict:
    """¿Hay una propuesta fresca de otro job que se pueda reutilizar?"""
    return await consultar_propuesta_disponible(db, ctx.company.id, periodo, tipo_libro)


@router.post("/jobs/{job_id}/resume", response_model=JobResponse)
def resume_job(
    job_id: int,
    ctx: ActiveContext = Depends(require_permission("sire.job.create")),
    db: Session = Depends(get_db),
) -> JobResponse:
    """Reencola una conciliación en error para que el worker la reprocese."""
    _chequear_limite_conciliacion(ctx.user.id)
    try:
        job = SqlReconciliationRepository(db).requeue_failed(job_id, ctx.company.id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conciliación no encontrada")
    return JobResponse.model_validate(job)


@router.get("/jobs/{job_id}", response_model=JobResponse)
def get_job(
    job_id: int,
    ctx: ActiveContext = Depends(require_permission("sire.job.read")),
    db: Session = Depends(get_db),
) -> JobResponse:
    repo = SqlReconciliationRepository(db)
    job = get_reconciliation_job(repo, job_id, ctx.company.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Conciliación no encontrada")
    return JobResponse.model_validate(job)


_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_CSV_COLUMNS = {
    "a": "csv_a_storage_path",
    "b": "csv_b_storage_path",
    "c": "csv_c_storage_path",
    "d": "csv_d_storage_path",
}


@router.get("/jobs/{job_id}/report")
def download_report(
    job_id: int,
    ctx: ActiveContext = Depends(require_permission("sire.job.read")),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> Response:
    report = SqlReconciliationRepository(db).get_report(job_id, ctx.company.id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Reporte no disponible")
    content = storage.read(report.storage_path)
    return Response(
        content=content,
        media_type=_XLSX_MEDIA,
        headers={"Content-Disposition": f'attachment; filename="{report.filename}"'},
    )


@router.get("/jobs/{job_id}/report/csv/{escenario}")
def download_csv(
    job_id: int,
    escenario: str,
    ctx: ActiveContext = Depends(require_permission("sire.job.read")),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> Response:
    columna = _CSV_COLUMNS.get(escenario.lower())
    if columna is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Escenario inválido (a, b, c o d)")
    report = SqlReconciliationRepository(db).get_report(job_id, ctx.company.id)
    storage_path = getattr(report, columna) if report else None
    if storage_path is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"CSV del escenario {escenario.upper()} no disponible",
        )
    content = storage.read(storage_path)
    filename = storage_path.split("/")[-1]
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ─────────────────────────── Credenciales SUNAT ───────────────────────────
@router.get("/credentials", response_model=CredentialsStatusResponse)
def credentials_status(
    ctx: ActiveContext = Depends(require_permission("sire.credentials.manage")),
    db: Session = Depends(get_db),
) -> CredentialsStatusResponse:
    estado = get_credentials_status(SqlCredentialsRepository(db), ctx.company.id)
    return CredentialsStatusResponse.model_validate(estado)


@router.put("/credentials", response_model=CredentialsStatusResponse)
def upsert_credentials(
    payload: CredentialsInput,
    ctx: ActiveContext = Depends(require_permission("sire.credentials.manage")),
    db: Session = Depends(get_db),
) -> CredentialsStatusResponse:
    repo = SqlCredentialsRepository(db)
    set_credentials(
        repo,
        company_id=ctx.company.id,
        user_id=ctx.user.id,
        usuario_sol=payload.usuario_sol,
        clave_sol=payload.clave_sol,
        client_id=payload.client_id,
        client_secret=payload.client_secret,
    )
    return CredentialsStatusResponse.model_validate(
        get_credentials_status(repo, ctx.company.id)
    )


# ─────────────────────────── Formato de archivo (mapeo) ───────────────────────────
@router.post("/file-mapping/analizar")
async def analizar_formato(
    tipo_libro: str = Form(...),
    archivo: UploadFile = File(...),
    ctx: ActiveContext = Depends(require_permission("sire.job.read")),
    db: Session = Depends(get_db),
) -> dict:
    """Analiza un archivo: columnas + mapeo propuesto + validación."""
    _validar_libro(tipo_libro)
    content = await archivo.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="El archivo está vacío")
    try:
        return file_mapping.analizar(
            SqlFileMappingRepository(db), ctx.company.id, tipo_libro, content
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/file-mapping/validar")
async def validar_formato(
    tipo_libro: str = Form(...),
    config: str = Form(..., description="JSON del mapeo a validar"),
    archivo: UploadFile = File(...),
    ctx: ActiveContext = Depends(require_permission("sire.job.read")),
) -> dict:
    _validar_libro(tipo_libro)
    cfg = _parse_json(config, "config")
    content = await archivo.read()
    return file_mapping.validar(content, cfg, tipo_libro)


@router.post("/file-mapping/guardar")
async def guardar_formato(
    tipo_libro: str = Form(...),
    config: str = Form(...),
    archivo: UploadFile = File(...),
    ctx: ActiveContext = Depends(require_permission("sire.mapping.manage")),
    db: Session = Depends(get_db),
) -> dict | None:
    """Guarda deliberadamente el formato de la empresa (valida antes de persistir)."""
    _validar_libro(tipo_libro)
    cfg = _parse_json(config, "config")
    content = await archivo.read()
    try:
        return file_mapping.guardar(
            SqlFileMappingRepository(db), ctx.company.id, tipo_libro, cfg, content
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/file-mapping")
def get_formato(
    tipo_libro: str,
    ctx: ActiveContext = Depends(require_permission("sire.job.read")),
    db: Session = Depends(get_db),
) -> dict | None:
    _validar_libro(tipo_libro)
    return file_mapping.get_saved(SqlFileMappingRepository(db), ctx.company.id, tipo_libro)


@router.delete("/file-mapping", status_code=status.HTTP_204_NO_CONTENT)
def delete_formato(
    tipo_libro: str,
    ctx: ActiveContext = Depends(require_permission("sire.mapping.manage")),
    db: Session = Depends(get_db),
) -> None:
    _validar_libro(tipo_libro)
    file_mapping.delete_saved(SqlFileMappingRepository(db), ctx.company.id, tipo_libro)
