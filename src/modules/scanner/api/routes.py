"""
Endpoints del módulo Scanner (montados bajo /api/scanner).

Fase 4a: gestión de documentos ya escaneados (listar/editar). El endpoint de
subida con OCR (`/upload/auto`) se incorpora en la Fase 4b.
"""
import io

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from src.modules.scanner.api.schemas import (
    ActualizarCamposInput,
    DocumentoItem,
    DocumentosExportInput,
    ScannerJobCreated,
    ScannerJobStatusResponse,
)
from src.modules.scanner.infrastructure.repositories import (
    SqlDocumentoRepository,
    SqlScannerJobRepository,
)
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import get_db
from src.platform.storage import get_storage
from src.platform.storage.base import FileStorage
from src.platform.tenancy.current_tenant import ActiveContext

router = APIRouter(tags=["scanner"], dependencies=[Depends(require_module("scanner"))])


@router.post("/upload/auto", response_model=ScannerJobCreated, status_code=status.HTTP_201_CREATED)
async def upload_auto(
    file: UploadFile = File(...),
    tipo: str = Form(""),
    ctx: ActiveContext = Depends(require_permission("scanner.doc.create")),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> ScannerJobCreated:
    """Sube un documento y lo encola para extracción por OCR (worker aparte).

    `tipo` opcional fuerza el tipo de documento (salta la auto-detección); vacío o
    'auto' = detección automática.
    """
    from src.modules.scanner.application.extraccion import ScannerExtractionError
    from src.modules.scanner.application.jobs import encolar_documento
    from src.modules.scanner.infrastructure.extractor.clasificador import ETIQUETAS

    tipo_forzado = tipo.strip()
    if tipo_forzado in ("", "auto"):
        tipo_forzado = ""
    elif tipo_forzado not in ETIQUETAS or tipo_forzado == "desconocido":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Tipo no válido: {tipo_forzado}")

    contenido = await file.read()
    try:
        job = encolar_documento(
            db, storage, ctx.company.id, ctx.user.id, file.filename or "archivo",
            contenido, tipo_forzado=tipo_forzado,
        )
    except ScannerExtractionError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return ScannerJobCreated(job_id=job.id, status=job.status)


@router.get("/jobs/{job_id}", response_model=ScannerJobStatusResponse)
def job_status(
    job_id: int,
    ctx: ActiveContext = Depends(require_permission("scanner.doc.read")),
    db: Session = Depends(get_db),
) -> ScannerJobStatusResponse:
    """Estado de un job de extracción (para el polling del frontend)."""
    job = SqlScannerJobRepository(db).get(job_id, ctx.company.id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job no encontrado")
    documento = None
    if job.documento_id is not None:
        doc = SqlDocumentoRepository(db).get(job.documento_id, ctx.company.id)
        if doc is not None:
            documento = DocumentoItem.model_validate(doc)
    return ScannerJobStatusResponse(
        id=job.id,
        status=job.status,
        error_message=job.error_message,
        documento=documento,
    )


@router.get("/tipos-documento")
def tipos_documento(
    _ctx: ActiveContext = Depends(require_permission("scanner.doc.read")),
) -> dict:
    """Tipos de documento soportados y sus campos (para la UI)."""
    from src.modules.scanner.infrastructure.extractor.campos import campos_de
    from src.modules.scanner.infrastructure.extractor.clasificador import ETIQUETAS

    return {
        tipo: {"etiqueta": label, "campos": campos_de(tipo)}
        for tipo, label in ETIQUETAS.items()
        if tipo != "desconocido"
    }


@router.get("/documentos", response_model=list[DocumentoItem])
def list_documentos(
    tipo: str | None = None,
    limit: int = 100,
    offset: int = 0,
    ctx: ActiveContext = Depends(require_permission("scanner.doc.read")),
    db: Session = Depends(get_db),
) -> list[DocumentoItem]:
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    docs = SqlDocumentoRepository(db).list_by_company(ctx.company.id, tipo, limit, offset)
    return [DocumentoItem.model_validate(d) for d in docs]


@router.post("/documentos/export")
def export_documentos(
    payload: DocumentosExportInput,
    _ctx: ActiveContext = Depends(require_permission("scanner.doc.read")),
) -> StreamingResponse:
    """Genera un .xlsx con las filas/columnas que el frontend está mostrando
    (documentos escalares o registros aplanados de planillas)."""
    from src.modules.scanner.infrastructure.report_documentos import generar_excel

    xlsx = generar_excel(payload.filas, payload.columnas, payload.labels)
    return StreamingResponse(
        io.BytesIO(xlsx),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="documentos.xlsx"'},
    )


@router.put("/documentos/{doc_id}", response_model=DocumentoItem)
def update_documento(
    doc_id: int,
    payload: ActualizarCamposInput,
    ctx: ActiveContext = Depends(require_permission("scanner.doc.update")),
    db: Session = Depends(get_db),
) -> DocumentoItem:
    doc = SqlDocumentoRepository(db).update_campos(doc_id, ctx.company.id, payload.campos)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Documento no encontrado")
    return DocumentoItem.model_validate(doc)
