"""
Endpoints de análisis de DataFilter (montados bajo /api/datafilter).

Tres transformaciones —columnas calculadas, buscar/reemplazar y eliminar
duplicados— cada una con "ver" (una página), "descargar" (archivo completo) y
"guardar" (dataset derivado nuevo, materializado en segundo plano). Más un perfil
estadístico por columna. Validación y mapeo de errores igual que el filtrado.
"""
import os

from fastapi import APIRouter, Depends, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from src.modules.datafilter.api.routes import http_error
from src.modules.datafilter.api.schemas import (
    ColumnStats,
    ComputeDownloadRequest,
    ComputeRequest,
    ComputeSaveRequest,
    DatasetSummary,
    DedupeDownloadRequest,
    DedupeRequest,
    DedupeResponse,
    DedupeSaveRequest,
    DownloadFormat,
    DownloadPresignResponse,
    PivotDownloadRequest,
    PivotRequest,
    PivotResponse,
    PivotSaveRequest,
    PreviewResponse,
    ReplaceDownloadRequest,
    ReplaceRequest,
    ReplaceSaveRequest,
    StatsRequest,
)
from src.modules.datafilter.application import analysis, query
from src.modules.datafilter.infrastructure import storage_access
from src.modules.datafilter.infrastructure.analysis_dispatch import encolar_materializacion
from src.modules.datafilter.infrastructure.duckdb_engine import QueryTimeout
from src.modules.datafilter.infrastructure.models import DatasetOrigin
from src.modules.datafilter.infrastructure.query_builder import InvalidFilterError
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import get_db
from src.platform.tenancy.current_tenant import ActiveContext

router = APIRouter(tags=["datafilter"], dependencies=[Depends(require_module("datafilter"))])

# Excepciones de negocio comunes a ver/descargar (el origen debe existir y estar listo).
_ERRORES = (
    query.DatasetNotFound, query.DatasetNotReady, query.DownloadTooLarge,
    InvalidFilterError, QueryTimeout,
)


def _delimiter_char(
    body: ComputeDownloadRequest | ReplaceDownloadRequest | DedupeDownloadRequest
    | PivotDownloadRequest,
):
    """Separador solo para TXT; CSV/XLSX lo ignoran."""
    return body.delimiter.char if body.format is DownloadFormat.TXT else None


def _descarga(ruta: str, media_type: str, nombre: str) -> FileResponse:
    """FileResponse que borra el temporal tras enviarlo."""
    return FileResponse(
        path=ruta, media_type=media_type, filename=nombre,
        background=BackgroundTask(os.remove, ruta),
    )


# Segundos de validez de la URL firmada de descarga directa desde R2.
_PRESIGN_EXPIRA = 2 * 3600


def _presign_r2(key: str, media_type: str, nombre: str) -> DownloadPresignResponse:
    """Firma una URL de descarga directa desde R2 para el export ya escrito."""
    url = storage_access.firmar_descarga(key, nombre, media_type, _PRESIGN_EXPIRA)
    return DownloadPresignResponse(mode="r2", url=url, filename=nombre)


def _guardar(
    db: Session, ctx: ActiveContext, source_id: int, nombre: str,
    origin: DatasetOrigin, spec: dict,
) -> DatasetSummary:
    """Crea el dataset derivado en PROCESSING y encola su materialización."""
    try:
        derivado = analysis.crear_derivado(
            db, source_id, ctx.company.id, ctx.user.id, nombre, origin
        )
    except (query.DatasetNotFound, query.DatasetNotReady) as exc:
        raise http_error(exc) from exc
    encolar_materializacion(derivado.id, source_id, ctx.company.id, spec)
    return DatasetSummary.from_model(derivado)


# ── Columnas calculadas ──────────────────────────────────────────────────

@router.post("/datasets/{dataset_id}/compute/preview", response_model=PreviewResponse)
def compute_preview(
    dataset_id: int,
    body: ComputeRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> PreviewResponse:
    """Una página con las columnas originales + las calculadas."""
    try:
        columns, rows, total = analysis.run_compute(
            db, dataset_id, ctx.company.id,
            filtro=body.filter, columnas=body.columns,
            orden_col=body.order_column, orden_dir=body.order_direction,
            limit=body.limit, offset=body.offset,
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return PreviewResponse(
        columns=columns, rows=rows, total_matched=total, limit=body.limit, offset=body.offset
    )


@router.post("/datasets/{dataset_id}/compute/download")
def compute_download(
    dataset_id: int,
    body: ComputeDownloadRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Descarga TODAS las filas con las columnas calculadas (CSV/XLSX/TXT)."""
    try:
        ruta, media, nombre = analysis.export_compute(
            db, dataset_id, ctx.company.id,
            filtro=body.filter, columnas=body.columns,
            formato=body.format.value, delimiter_char=_delimiter_char(body),
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return _descarga(ruta, media, nombre)


@router.post(
    "/datasets/{dataset_id}/compute/save",
    response_model=DatasetSummary, status_code=status.HTTP_201_CREATED,
)
def compute_save(
    dataset_id: int,
    body: ComputeSaveRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.create")),
    db: Session = Depends(get_db),
) -> DatasetSummary:
    """Guarda las columnas calculadas como un dataset nuevo (se materializa en segundo plano)."""
    spec = {"kind": DatasetOrigin.computed.value, "filtro": body.filter, "columnas": body.columns}
    return _guardar(db, ctx, dataset_id, body.name, DatasetOrigin.computed, spec)


# ── Buscar y reemplazar por columna ──────────────────────────────────────

@router.post("/datasets/{dataset_id}/replace/preview", response_model=PreviewResponse)
def replace_preview(
    dataset_id: int,
    body: ReplaceRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> PreviewResponse:
    """Una página con las correcciones aplicadas (todas las columnas)."""
    try:
        columns, rows, total = analysis.run_replace(
            db, dataset_id, ctx.company.id,
            filtro=body.filter, replacements=body.replacements,
            orden_col=body.order_column, orden_dir=body.order_direction,
            limit=body.limit, offset=body.offset,
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return PreviewResponse(
        columns=columns, rows=rows, total_matched=total, limit=body.limit, offset=body.offset
    )


@router.post("/datasets/{dataset_id}/replace/download")
def replace_download(
    dataset_id: int,
    body: ReplaceDownloadRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Descarga TODAS las filas ya corregidas (CSV/XLSX/TXT)."""
    try:
        ruta, media, nombre = analysis.export_replace(
            db, dataset_id, ctx.company.id,
            filtro=body.filter, replacements=body.replacements,
            formato=body.format.value, delimiter_char=_delimiter_char(body),
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return _descarga(ruta, media, nombre)


@router.post(
    "/datasets/{dataset_id}/replace/save",
    response_model=DatasetSummary, status_code=status.HTTP_201_CREATED,
)
def replace_save(
    dataset_id: int,
    body: ReplaceSaveRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.create")),
    db: Session = Depends(get_db),
) -> DatasetSummary:
    """Guarda el resultado corregido como un dataset nuevo (se materializa en segundo plano)."""
    spec = {
        "kind": DatasetOrigin.replaced.value,
        "filtro": body.filter, "replacements": body.replacements,
    }
    return _guardar(db, ctx, dataset_id, body.name, DatasetOrigin.replaced, spec)


# ── Eliminar duplicados ──────────────────────────────────────────────────

@router.post("/datasets/{dataset_id}/dedupe/preview", response_model=DedupeResponse)
def dedupe_preview(
    dataset_id: int,
    body: DedupeRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> DedupeResponse:
    """Una página sin duplicados + cuántas filas quedaron y cuántas había."""
    try:
        columns, rows, total, original = analysis.run_dedupe(
            db, dataset_id, ctx.company.id,
            filtro=body.filter, key_columns=body.key_columns,
            orden_col=body.order_column, orden_dir=body.order_direction,
            limit=body.limit, offset=body.offset,
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return DedupeResponse(
        columns=columns, rows=rows, total_matched=total, total_original=original,
        limit=body.limit, offset=body.offset,
    )


@router.post("/datasets/{dataset_id}/dedupe/download")
def dedupe_download(
    dataset_id: int,
    body: DedupeDownloadRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Descarga TODAS las filas sin duplicados (CSV/XLSX/TXT)."""
    try:
        ruta, media, nombre = analysis.export_dedupe(
            db, dataset_id, ctx.company.id,
            filtro=body.filter, key_columns=body.key_columns,
            formato=body.format.value, delimiter_char=_delimiter_char(body),
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return _descarga(ruta, media, nombre)


@router.post(
    "/datasets/{dataset_id}/dedupe/save",
    response_model=DatasetSummary, status_code=status.HTTP_201_CREATED,
)
def dedupe_save(
    dataset_id: int,
    body: DedupeSaveRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.create")),
    db: Session = Depends(get_db),
) -> DatasetSummary:
    """Guarda el resultado sin duplicados como un dataset nuevo (materializado en segundo plano)."""
    spec = {
        "kind": DatasetOrigin.deduped.value,
        "filtro": body.filter, "key_columns": body.key_columns,
    }
    return _guardar(db, ctx, dataset_id, body.name, DatasetOrigin.deduped, spec)


# ── Tablas dinámicas (pivote) ────────────────────────────────────────────

@router.post("/datasets/{dataset_id}/pivot/preview", response_model=PivotResponse)
def pivot_preview(
    dataset_id: int,
    body: PivotRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> PivotResponse:
    """Una página del reporte dinámico + total de filas agrupadas + Total general."""
    try:
        columns, rows, total, totals = analysis.run_pivot(
            db, dataset_id, ctx.company.id, spec=body, limit=body.limit, offset=body.offset
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return PivotResponse(
        columns=columns, rows=rows, total_matched=total,
        limit=body.limit, offset=body.offset, totals=totals,
    )


@router.post("/datasets/{dataset_id}/pivot/download")
def pivot_download(
    dataset_id: int,
    body: PivotDownloadRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Descarga el reporte dinámico COMPLETO (CSV/XLSX/TXT)."""
    try:
        ruta, media, nombre = analysis.export_pivot(
            db, dataset_id, ctx.company.id,
            spec=body, formato=body.format.value, delimiter_char=_delimiter_char(body),
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return _descarga(ruta, media, nombre)


@router.post(
    "/datasets/{dataset_id}/pivot/save",
    response_model=DatasetSummary, status_code=status.HTTP_201_CREATED,
)
def pivot_save(
    dataset_id: int,
    body: PivotSaveRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.create")),
    db: Session = Depends(get_db),
) -> DatasetSummary:
    """Guarda el reporte dinámico como un dataset nuevo (materializado en segundo plano)."""
    spec = {"kind": DatasetOrigin.pivot.value, "pivot_spec": body}
    return _guardar(db, ctx, dataset_id, body.name, DatasetOrigin.pivot, spec)


# ── Estadísticas por columna ─────────────────────────────────────────────

@router.post("/datasets/{dataset_id}/stats", response_model=ColumnStats)
def column_stats(
    dataset_id: int,
    body: StatsRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> ColumnStats:
    """Perfil descriptivo de una columna: conteos, mín/máx, suma/promedio y top valores."""
    try:
        perfil = analysis.run_stats(
            db, dataset_id, ctx.company.id, filtro=body.filter, column=body.column
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return ColumnStats(**perfil)


# ── Descargas directas desde R2 (URL firmada) ─────────────────────────────
# Mismo patrón que la descarga filtrada: escriben el resultado DIRECTO a R2 y
# devuelven una URL firmada; en dev/local devuelven mode=single (descarga clásica).

@router.post(
    "/datasets/{dataset_id}/compute/download/presign", response_model=DownloadPresignResponse
)
def compute_download_presign(
    dataset_id: int,
    body: ComputeDownloadRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> DownloadPresignResponse:
    if not storage_access.usa_s3():
        return DownloadPresignResponse(mode="single")
    try:
        key, media, nombre = analysis.export_compute(
            db, dataset_id, ctx.company.id,
            filtro=body.filter, columnas=body.columns,
            formato=body.format.value, delimiter_char=_delimiter_char(body), a_r2=True,
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return _presign_r2(key, media, nombre)


@router.post(
    "/datasets/{dataset_id}/replace/download/presign", response_model=DownloadPresignResponse
)
def replace_download_presign(
    dataset_id: int,
    body: ReplaceDownloadRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> DownloadPresignResponse:
    if not storage_access.usa_s3():
        return DownloadPresignResponse(mode="single")
    try:
        key, media, nombre = analysis.export_replace(
            db, dataset_id, ctx.company.id,
            filtro=body.filter, replacements=body.replacements,
            formato=body.format.value, delimiter_char=_delimiter_char(body), a_r2=True,
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return _presign_r2(key, media, nombre)


@router.post(
    "/datasets/{dataset_id}/dedupe/download/presign", response_model=DownloadPresignResponse
)
def dedupe_download_presign(
    dataset_id: int,
    body: DedupeDownloadRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> DownloadPresignResponse:
    if not storage_access.usa_s3():
        return DownloadPresignResponse(mode="single")
    try:
        key, media, nombre = analysis.export_dedupe(
            db, dataset_id, ctx.company.id,
            filtro=body.filter, key_columns=body.key_columns,
            formato=body.format.value, delimiter_char=_delimiter_char(body), a_r2=True,
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return _presign_r2(key, media, nombre)


@router.post(
    "/datasets/{dataset_id}/pivot/download/presign", response_model=DownloadPresignResponse
)
def pivot_download_presign(
    dataset_id: int,
    body: PivotDownloadRequest,
    ctx: ActiveContext = Depends(require_permission("datafilter.dataset.read")),
    db: Session = Depends(get_db),
) -> DownloadPresignResponse:
    if not storage_access.usa_s3():
        return DownloadPresignResponse(mode="single")
    try:
        key, media, nombre = analysis.export_pivot(
            db, dataset_id, ctx.company.id,
            spec=body, formato=body.format.value, delimiter_char=_delimiter_char(body), a_r2=True,
        )
    except _ERRORES as exc:
        raise http_error(exc) from exc
    return _presign_r2(key, media, nombre)
