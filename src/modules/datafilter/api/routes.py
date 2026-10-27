"""
Endpoints del módulo DataFilter (montados bajo /api/datafilter).

Fase 2 (ingesta): subir un archivo (CSV/TXT/Excel) → se convierte a Parquet en un
subproceso efímero → queda listo para filtrar/descargar. Listar y consultar estado.
El filtrado y la descarga llegan en la Fase 3.
"""
import math
import os

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
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
    SheetSelection,
    UploadAbortRequest,
    UploadCompleteRequest,
    UploadInitRequest,
    UploadInitResponse,
    UploadPartUrl,
)
from src.modules.datafilter.application import query
from src.modules.datafilter.infrastructure import storage_access
from src.modules.datafilter.infrastructure.duckdb_engine import QueryTimeout
from src.modules.datafilter.infrastructure.ingest_dispatch import encolar_ingesta
from src.modules.datafilter.infrastructure.models import DatasetStatus
from src.modules.datafilter.infrastructure.query_builder import InvalidFilterError
from src.modules.datafilter.infrastructure.repositories import SqlDatasetRepository
from src.platform.authorization.deps import require_module, require_permission
from src.platform.config.settings import settings
from src.platform.database.session import get_db
from src.platform.storage import get_storage
from src.platform.storage.base import FileStorage
from src.platform.tenancy.current_tenant import ActiveContext

router = APIRouter(tags=["datafilter"], dependencies=[Depends(require_module("datafilter"))])

_EXTENSIONES_OK = {".csv", ".txt", ".xlsx", ".xls"}
_CHUNK = 1024 * 1024  # 1 MiB por lectura (subida en streaming, sin cargar todo en RAM)
_PART_SIZE = 100 * 1024 * 1024  # 100 MiB por parte en la subida directa a R2 (multipart)
_PRESIGN_EXPIRA = 2 * 3600  # 2 h: margen para subir muchas partes con conexión lenta


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
    max_bytes = settings.DATAFILTER_MAX_UPLOAD_MB * 1024 * 1024
    tamano = 0
    excede = False
    with storage.open_write(key) as salida:
        while chunk := await file.read(_CHUNK):
            tamano += len(chunk)
            if tamano > max_bytes:
                excede = True
                break  # deja de leer: no transmitimos un archivo que ya rechazamos
            salida.write(chunk)

    if excede:
        try:
            storage_access.delete(key)
        except Exception:  # limpieza best-effort del parcial
            pass
        repo.delete(ds.id, ctx.company.id)
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo supera el máximo de {settings.DATAFILTER_MAX_UPLOAD_MB} MB.",
        )

    repo.set_uploaded(ds.id, tamano, key)
    encolar_ingesta(ds.id)
    return DatasetSummary.from_model(ds)


# ── Subida directa a R2 (multipart con URLs firmadas) ──────────────────────
# Evita el límite/timeout del borde del hosting para archivos de varios GB: el
# navegador sube las partes DIRECTO a R2 y el backend solo orquesta.


@router.post("/datasets/upload/init", response_model=UploadInitResponse)
def iniciar_subida(
    payload: UploadInitRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.create")),
    db: Session = Depends(get_db),
) -> UploadInitResponse:
    """Inicia la subida directa a R2 en partes. En dev/local (storage no-S3) devuelve
    `mode=single` para que el front use el flujo clásico por el backend."""
    extension = os.path.splitext(payload.filename)[1].lower()
    if extension not in _EXTENSIONES_OK:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Formato no soportado. Sube un archivo CSV, TXT o Excel (.xlsx/.xls).",
        )
    max_bytes = settings.DATAFILTER_MAX_UPLOAD_MB * 1024 * 1024
    if payload.size_bytes > max_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo supera el máximo de {settings.DATAFILTER_MAX_UPLOAD_MB} MB.",
        )
    if not storage_access.usa_s3():
        return UploadInitResponse(mode="single")

    repo = SqlDatasetRepository(db)
    ds = repo.create_uploading(
        company_id=ctx.company.id,
        created_by_id=ctx.user.id,
        original_filename=payload.filename,
        extension=extension,
    )
    key = storage_access.source_key(ctx.company.id, ds.id, extension)
    upload_id = storage_access.crear_multipart(key)
    repo.set_multipart(ds.id, key, upload_id)

    total_partes = max(1, math.ceil(payload.size_bytes / _PART_SIZE))
    partes = [
        UploadPartUrl(
            part_number=n,
            url=storage_access.firmar_parte(key, upload_id, n, _PRESIGN_EXPIRA),
        )
        for n in range(1, total_partes + 1)
    ]
    return UploadInitResponse(
        mode="multipart", dataset_id=ds.id, part_size=_PART_SIZE, parts=partes
    )


@router.post(
    "/datasets/upload/complete",
    response_model=DatasetSummary,
    status_code=status.HTTP_201_CREATED,
)
def completar_subida(
    payload: UploadCompleteRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.create")),
    db: Session = Depends(get_db),
) -> DatasetSummary:
    """Ensambla las partes en R2, valida el tamaño real y encola la ingesta."""
    repo = SqlDatasetRepository(db)
    ds = repo.get(payload.dataset_id, ctx.company.id)
    if (
        ds is None
        or ds.status != DatasetStatus.uploading.value
        or not ds.source_path
        or not ds.multipart_upload_id
    ):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Subida no encontrada o ya finalizada"
        )

    partes = [
        {"PartNumber": p.part_number, "ETag": p.etag}
        for p in sorted(payload.parts, key=lambda parte: parte.part_number)
    ]
    try:
        storage_access.completar_multipart(ds.source_path, ds.multipart_upload_id, partes)
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de R2 se traduce a 400
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="No se pudo completar la subida"
        ) from exc

    tamano = storage_access.tamano(ds.source_path)
    max_bytes = settings.DATAFILTER_MAX_UPLOAD_MB * 1024 * 1024
    if tamano > max_bytes:
        storage_access.delete(ds.source_path)
        repo.delete(ds.id, ctx.company.id)
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"El archivo supera el máximo de {settings.DATAFILTER_MAX_UPLOAD_MB} MB.",
        )

    listo = repo.complete_upload(ds.id, ctx.company.id, tamano)
    if listo is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Subida no encontrada o ya finalizada"
        )
    encolar_ingesta(listo.id)
    return DatasetSummary.from_model(listo)


@router.post("/datasets/upload/abort", status_code=status.HTTP_204_NO_CONTENT)
def abortar_subida(
    payload: UploadAbortRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.create")),
    db: Session = Depends(get_db),
) -> Response:
    """Cancela una subida directa en curso y limpia el dataset provisional."""
    repo = SqlDatasetRepository(db)
    ds = repo.get(payload.dataset_id, ctx.company.id)
    if ds is not None and ds.status == DatasetStatus.uploading.value:
        if ds.source_path and ds.multipart_upload_id:
            try:
                storage_access.abortar_multipart(ds.source_path, ds.multipart_upload_id)
            except Exception:  # noqa: BLE001 — limpieza best-effort
                pass
        repo.delete(ds.id, ctx.company.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/datasets/{dataset_id}/sheet",
    response_model=DatasetDetail, status_code=status.HTTP_202_ACCEPTED,
)
def cambiar_hoja(
    dataset_id: int,
    body: SheetSelection,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.create")),
    db: Session = Depends(get_db),
) -> DatasetDetail:
    """Re-ingesta un Excel multi-hoja usando otra hoja (vuelve a procesarse en 2º plano)."""
    repo = SqlDatasetRepository(db)
    try:
        ds = repo.request_sheet_change(dataset_id, ctx.company.id, body.sheet)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if ds is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado")
    encolar_ingesta(dataset_id)
    return DatasetDetail.from_model(ds)


def http_error(exc: Exception) -> HTTPException:
    """Traduce los errores de filtrado/análisis a respuestas HTTP. Compartido con
    las rutas de análisis (mismas familias de excepción)."""
    if isinstance(exc, query.DatasetNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail="Dataset no encontrado")
    if isinstance(exc, query.DatasetNotReady):
        return HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"El dataset aún no está listo (estado: {exc.status}).",
        )
    if isinstance(exc, (InvalidFilterError, query.DownloadTooLarge)):
        return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if isinstance(exc, QueryTimeout):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La consulta tardó demasiado y se canceló. Aplica un filtro o reduce el rango.",
        )
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
    except (query.DatasetNotFound, query.DatasetNotReady, InvalidFilterError, QueryTimeout) as exc:
        raise http_error(exc) from exc
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
    except (query.DatasetNotFound, query.DatasetNotReady, InvalidFilterError, QueryTimeout) as exc:
        raise http_error(exc) from exc
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
            InvalidFilterError, QueryTimeout) as exc:
        raise http_error(exc) from exc
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
