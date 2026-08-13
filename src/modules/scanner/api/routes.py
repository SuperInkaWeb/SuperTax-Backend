"""
Endpoints del módulo Scanner (montados bajo /api/scanner).

Fase 4a: gestión de documentos ya escaneados (listar/editar). El endpoint de
subida con OCR (`/upload/auto`) se incorpora en la Fase 4b.
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from src.modules.scanner.api.schemas import ActualizarCamposInput, DocumentoItem
from src.modules.scanner.infrastructure.repositories import SqlDocumentoRepository
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import get_db
from src.platform.storage import get_storage
from src.platform.storage.base import FileStorage
from src.platform.tenancy.current_tenant import ActiveContext

router = APIRouter(tags=["scanner"], dependencies=[Depends(require_module("scanner"))])


@router.post("/upload/auto")
async def upload_auto(
    file: UploadFile = File(...),
    ctx: ActiveContext = Depends(require_permission("scanner.doc.create")),
    db: Session = Depends(get_db),
    storage: FileStorage = Depends(get_storage),
) -> dict:
    """Sube un documento, lo clasifica y extrae sus campos por OCR."""
    from src.modules.scanner.application.extraccion import procesar_documento

    return await procesar_documento(db, storage, ctx.company.id, ctx.user.id, file)


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
