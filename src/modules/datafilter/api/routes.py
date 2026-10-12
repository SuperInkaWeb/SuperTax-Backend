"""
Endpoints del módulo DataFilter (montados bajo /api/datafilter).

Fase 2 (ingesta): subir un archivo (CSV/TXT/Excel) → se convierte a Parquet en un
subproceso efímero → queda listo para filtrar/descargar. Listar y consultar estado.
El filtrado y la descarga llegan en la Fase 3.
"""
import os

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from src.modules.datafilter.api.schemas import (
    DatasetDetail,
    DatasetSummary,
    DistinctValuesResponse,
    DownloadFormat,
    DownloadRequest,
    PreviewRequest,
    PreviewResponse,
)
from src.modules.datafilter.application import query
from src.modules.datafilter.infrastructure import storage_access
from src.modules.datafilter.infrastructure.ingest_dispatch import encolar_ingesta
from src.modules.datafilter.infrastructure.query_builder import InvalidFilterError
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


def _http(exc: Exception) -> HTTPException:
    """Traduce los errores del servicio de filtrado a respuestas HTTP."""
    if isinstance(exc, query.DatasetNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado")
    if isinstance(exc, query.DatasetNotReady):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"El dataset aún no está listo (estado: {exc.status}).",
        )
    if isinstance(exc, (InvalidFilterError, query.DownloadTooLarge)):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    raise exc


# Endpoints SÍNCRONOS a propósito: la consulta DuckDB (sobre R2) es bloqueante y puede
# tardar; con `def`, FastAPI la corre en su threadpool y el event loop sigue libre.
@router.post("/datasets/{dataset_id}/preview", response_model=PreviewResponse)
def previsualizar(
    dataset_id: int,
    request: PreviewRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> PreviewResponse:
    """Una página de filas que cumplen el filtro + total de coincidencias."""
    try:
        columns, rows, total = query.preview(
            db, dataset_id, ctx.company.id,
            filtro=request.filter, seleccion=request.select, orden=request.sort,
            limit=request.limit, offset=request.offset, search=request.search,
        )
    except (query.DatasetNotFound, query.DatasetNotReady, InvalidFilterError) as exc:
        raise _http(exc) from exc
    return PreviewResponse(
        columns=columns, rows=rows, total_matched=total,
        limit=request.limit, offset=request.offset,
    )


@router.get(
    "/datasets/{dataset_id}/columns/{column}/distinct", response_model=DistinctValuesResponse
)
def valores_distintos(
    dataset_id: int,
    column: str,
    search: str | None = Query(default=None, max_length=200),
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> DistinctValuesResponse:
    """Valores únicos de una columna, para el filtro tipo Excel (casillas)."""
    try:
        values, truncated = query.distinct_values(db, dataset_id, ctx.company.id, column, search)
    except (query.DatasetNotFound, query.DatasetNotReady, InvalidFilterError) as exc:
        raise _http(exc) from exc
    return DistinctValuesResponse(column=column, values=values, truncated=truncated)


@router.post("/datasets/{dataset_id}/download")
def descargar(
    dataset_id: int,
    body: DownloadRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Aplica el filtro, escribe el resultado en un temporal y lo transmite (CSV/TXT/XLSX)."""
    delimiter = body.delimiter.char if body.format is DownloadFormat.TXT else None
    try:
        ruta, media_type, nombre = query.build_export(
            db, dataset_id, ctx.company.id,
            filtro=body.filter, seleccion=body.select, orden=body.sort, search=body.search,
            formato=body.format.value, delimiter_char=delimiter,
        )
    except (query.DatasetNotFound, query.DatasetNotReady, query.DownloadTooLarge,
            InvalidFilterError) as exc:
        raise _http(exc) from exc
    return FileResponse(
        path=ruta, media_type=media_type, filename=nombre,
        background=BackgroundTask(os.remove, ruta),  # borra el temporal tras enviarlo
    )


@router.delete("/datasets/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def eliminar_dataset(
    dataset_id: int,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.delete")),
    db: Session = Depends(get_db),
) -> None:
    """Borra el dataset y sus archivos (crudo + Parquet) de la empresa activa."""
    if not query.eliminar(db, dataset_id, ctx.company.id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado")
