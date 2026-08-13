"""
Endpoints del módulo Scanner (montados bajo /api/scanner).

Fase 4a: gestión de documentos ya escaneados (listar/editar). El endpoint de
subida con OCR (`/upload/auto`) se incorpora en la Fase 4b.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.modules.scanner.api.schemas import ActualizarCamposInput, DocumentoItem
from src.modules.scanner.infrastructure.repositories import SqlDocumentoRepository
from src.platform.authorization.deps import require_module, require_permission
from src.platform.database.session import get_db
from src.platform.tenancy.current_tenant import ActiveContext

router = APIRouter(tags=["scanner"], dependencies=[Depends(require_module("scanner"))])


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
