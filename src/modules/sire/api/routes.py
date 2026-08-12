"""
Endpoints del módulo SIRE (montados bajo /api/sire).

Doble puerta de seguridad:
- `require_module("sire")` a nivel de router: la empresa debe tener el módulo.
- `require_permission("sire.job.read")` por endpoint: el rol debe poder.
Todo se filtra por la empresa activa (`ctx.company.id`) → aislamiento multi-tenant.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.modules.sire.api.schemas import JobResponse
from src.modules.sire.application.use_cases import (
    get_reconciliation_job,
    list_reconciliation_jobs,
)
from src.modules.sire.infrastructure.repositories import SqlReconciliationRepository
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import get_db
from src.platform.tenancy.current_tenant import ActiveContext

router = APIRouter(tags=["sire"], dependencies=[Depends(require_module("sire"))])


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
