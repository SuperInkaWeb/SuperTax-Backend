"""
Endpoints del módulo DataFilter (montados bajo /api/datafilter).

Fase 2 (ingesta): subir un archivo (CSV/TXT/Excel) → se convierte a Parquet en un
subproceso efímero → queda listo para filtrar/descargar. Listar y consultar estado.
El filtrado y la descarga llegan en la Fase 3.
"""
import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from src.modules.datafilter.api.schemas import DatasetDetail, DatasetSummary
from src.modules.datafilter.infrastructure import storage_access
from src.modules.datafilter.infrastructure.ingest_dispatch import encolar_ingesta
from src.modules.datafilter.infrastructure.repositories import SqlDatasetRepository
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import get_db
from src.platform.storage import get_storage
from src.platform.storage.base import FileStorage
from src.platform.tenancy.current_tenant import ActiveContext

router = APIRouter(tags=["datafilter"], dependencies=[Depends(require_module("datafilter"))])

_EXTENSIONES_OK = {".csv", ".txt", ".xlsx", ".xls"}
_CHUNK = 1024 * 1024  # 1 MiB por lectura (subida en streaming, sin cargar todo en RAM)


@router.get("/datasets", response_model=list[DatasetSummary])
def listar_datasets(
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> list[DatasetSummary]:
    """Datasets de la empresa activa, del más reciente al más antiguo."""
    datasets = SqlDatasetRepository(db).list_by_company(ctx.company.id)
    return [DatasetSummary.from_model(d) for d in datasets]


@router.get("/datasets/{dataset_id}", response_model=DatasetDetail)
def ver_dataset(
    dataset_id: int,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> DatasetDetail:
    """Detalle y estado de un dataset (para seguir el progreso de la ingesta)."""
    ds = SqlDatasetRepository(db).get(dataset_id, ctx.company.id)
    if ds is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado")
    return DatasetDetail.from_model(ds)


@router.post("/datasets/upload", response_model=DatasetSummary, status_code=status.HTTP_201_CREATED)
async def subir_dataset(
    file: UploadFile = File(...),
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.create")),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> DatasetSummary:
    """Sube un archivo grande y encola su conversión a Parquet (subproceso efímero).

    Acepta CSV, TXT y Excel (.xlsx/.xls). El archivo se guarda en streaming (no se
    carga entero en RAM). Devuelve el dataset en estado `pending/processing`; el
    frontend consulta `GET /datasets/{id}` hasta que quede `ready`.
    """
    filename = file.filename or "archivo"
    extension = os.path.splitext(filename)[1].lower()
    if extension not in _EXTENSIONES_OK:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Formato no soportado. Sube un archivo CSV, TXT o Excel (.xlsx/.xls).",
        )

    repo = SqlDatasetRepository(db)
    ds = repo.create(
        company_id=ctx.company.id,
        created_by_id=ctx.user.id,
        original_filename=filename,
        extension=extension,
    )

    key = storage_access.source_key(ctx.company.id, ds.id, extension)
    tamano = 0
    with storage.open_write(key) as salida:
        while chunk := await file.read(_CHUNK):
            salida.write(chunk)
            tamano += len(chunk)
    repo.set_uploaded(ds.id, tamano, key)

    encolar_ingesta(ds.id)
    return DatasetSummary.from_model(ds)
