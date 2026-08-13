"""
Endpoints del módulo SUNAT (montados bajo /api/sunat).

Doble puerta: `require_module("sunat")` + `require_permission(...)`, todo filtrado
por la empresa activa. La ejecución de descargas (Playwright + SSE) llega en 3b;
aquí están credenciales, historial y estado de Google Drive.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.modules.sunat.api.schemas import (
    CredentialsInput,
    CredentialsStatusResponse,
    DriveStatusResponse,
    JobResultResponse,
)
from src.modules.sunat.application.credentials import (
    get_credentials_status,
    set_credentials,
)
from src.modules.sunat.infrastructure.repositories import (
    SqlDriveTokenRepository,
    SqlJobResultRepository,
    SqlSunatCredentialsRepository,
)
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import get_db
from src.platform.tenancy.current_tenant import ActiveContext

router = APIRouter(tags=["sunat"], dependencies=[Depends(require_module("sunat"))])


@router.get("/jobs", response_model=list[JobResultResponse])
def list_jobs(
    limit: int = 100,
    offset: int = 0,
    ctx: ActiveContext = Depends(require_permission("sunat.job.read")),
    db: Session = Depends(get_db),
) -> list[JobResultResponse]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    resultados = SqlJobResultRepository(db).list_by_company(ctx.company.id, limit, offset)
    return [JobResultResponse.model_validate(r) for r in resultados]


@router.get("/credentials", response_model=CredentialsStatusResponse)
def credentials_status(
    ctx: ActiveContext = Depends(require_permission("sunat.credentials.manage")),
    db: Session = Depends(get_db),
) -> CredentialsStatusResponse:
    estado = get_credentials_status(SqlSunatCredentialsRepository(db), ctx.company.id)
    return CredentialsStatusResponse.model_validate(estado)


@router.put("/credentials", response_model=CredentialsStatusResponse)
def upsert_credentials(
    payload: CredentialsInput,
    ctx: ActiveContext = Depends(require_permission("sunat.credentials.manage")),
    db: Session = Depends(get_db),
) -> CredentialsStatusResponse:
    repo = SqlSunatCredentialsRepository(db)
    set_credentials(
        repo,
        company_id=ctx.company.id,
        user_id=ctx.user.id,
        ruc=payload.ruc,
        usuario=payload.usuario,
        clave=payload.clave,
    )
    return CredentialsStatusResponse.model_validate(
        get_credentials_status(repo, ctx.company.id)
    )


@router.get("/drive", response_model=DriveStatusResponse)
def drive_status(
    ctx: ActiveContext = Depends(require_permission("sunat.drive.manage")),
    db: Session = Depends(get_db),
) -> DriveStatusResponse:
    token = SqlDriveTokenRepository(db).get(ctx.company.id)
    return DriveStatusResponse(connected=token is not None)
