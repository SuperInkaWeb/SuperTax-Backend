"""
Endpoints del módulo SIRE (montados bajo /api/sire).

Doble puerta de seguridad:
- `require_module("sire")` a nivel de router: la empresa debe tener el módulo.
- `require_permission(...)` por endpoint: el rol debe poder.
Todo se filtra por la empresa activa (`ctx.company.id`) → aislamiento multi-tenant.
"""
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from src.modules.sire.api.schemas import (
    CredentialsInput,
    CredentialsStatusResponse,
    JobResponse,
)
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
from src.modules.sire.infrastructure.repositories import (
    SqlCredentialsRepository,
    SqlReconciliationRepository,
)
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import get_db
from src.platform.storage import get_storage
from src.platform.storage.base import FileStorage
from src.platform.tenancy.current_tenant import ActiveContext

router = APIRouter(tags=["sire"], dependencies=[Depends(require_module("sire"))])


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
    ctx: ActiveContext = Depends(require_permission("sire.job.create")),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> JobResponse:
    content = await archivo.read()
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
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
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
