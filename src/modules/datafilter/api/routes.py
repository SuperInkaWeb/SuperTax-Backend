"""
Endpoints del módulo DataFilter (montados bajo /api/datafilter).

Fase 1 (andamiaje): listar los datasets de la empresa activa. La subida/ingesta,
el filtrado y la descarga llegan en las fases siguientes.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.modules.datafilter.api.schemas import DatasetSummary
from src.modules.datafilter.infrastructure.repositories import SqlDatasetRepository
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import get_db
from src.platform.tenancy.current_tenant import ActiveContext

router = APIRouter(tags=["datafilter"], dependencies=[Depends(require_module("datafilter"))])


@router.get("/datasets", response_model=list[DatasetSummary])
def listar_datasets(
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> list[DatasetSummary]:
    """Datasets de la empresa activa, del más reciente al más antiguo."""
    datasets = SqlDatasetRepository(db).list_by_company(ctx.company.id)
    return [DatasetSummary.from_model(d) for d in datasets]
