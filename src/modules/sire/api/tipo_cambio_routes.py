"""
Administración del tipo de cambio (GLOBAL, solo SuperAdmin).

Va en un router propio, NO bajo `require_module("sire")`: el TC es nacional (una
tabla para todas las empresas), así que no depende de una empresa activa y por eso
no exige la cabecera X-Company-Id. Son endpoints síncronos (def): las consultas a
fuentes externas usan httpx sync y FastAPI las corre en su threadpool.
"""
from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from src.modules.sire.api.schemas import (
    TipoCambioEdit,
    TipoCambioItem,
    TipoCambioRefrescar,
)
from src.modules.sire.infrastructure.tipo_cambio import repositorio as tc_repo
from src.modules.sire.infrastructure.tipo_cambio import sincronizador as tc_sync
from src.platform.authorization.deps import require_platform_admin
from src.platform.database.session import get_db

router = APIRouter(prefix="/api/sire/tipo-cambio", tags=["sire-tipo-cambio"])

_RANGO_MAX_DIAS = 400


def _validar_rango(desde: date, hasta: date) -> None:
    if hasta < desde:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="'hasta' debe ser >= 'desde'")
    if (hasta - desde).days > _RANGO_MAX_DIAS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"El rango no puede superar {_RANGO_MAX_DIAS} días",
        )


@router.get("", response_model=list[TipoCambioItem])
def list_tipo_cambio(
    desde: date,
    hasta: date,
    _admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> list[TipoCambioItem]:
    _validar_rango(desde, hasta)
    return [TipoCambioItem.model_validate(f) for f in tc_repo.listar_rango(db, desde, hasta)]


@router.post("/refrescar")
def refrescar_tipo_cambio(
    payload: TipoCambioRefrescar,
    _admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Baja del BCRP el TC del rango (solo útil desde una IP no bloqueada)."""
    _validar_rango(payload.desde, payload.hasta)
    try:
        actualizados = tc_sync.sincronizar_rango(db, payload.desde, payload.hasta)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"No se pudo consultar el BCRP: {exc}"
        )
    return {"actualizados": actualizados}


@router.post("/importar")
async def importar_tipo_cambio(
    archivo: UploadFile = File(..., description="Excel de TC (hoja TC: Día, Compra, Venta)"),
    _admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Carga/actualiza el TC desde un Excel (forma SUNAT). Respeta overrides manuales."""
    contenido = await archivo.read()
    if not contenido:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="El archivo está vacío")
    try:
        actualizados = tc_sync.importar_excel(db, contenido)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return {"actualizados": actualizados}


@router.post("/traer-hoy")
def traer_tc_hoy(
    _admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> dict:
    """Baja el TC del día desde el txt oficial de SUNAT y lo guarda."""
    try:
        actualizados = tc_sync.capturar_tc_hoy(db)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"No se pudo consultar SUNAT: {exc}"
        )
    return {"actualizados": actualizados}


@router.put("/{fecha}", response_model=TipoCambioItem)
def editar_tipo_cambio(
    fecha: date,
    payload: TipoCambioEdit,
    _admin=Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> TipoCambioItem:
    """Override manual de un día (fuente='manual'); el refresco automático no lo pisa."""
    fila = tc_repo.editar_manual(db, fecha, payload.compra, payload.venta)
    return TipoCambioItem.model_validate(fila)
