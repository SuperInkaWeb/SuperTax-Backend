"""Endpoints de onboarding (solicitudes de acceso)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.deps import require_platform_admin
from src.platform.database.session import get_db
from src.platform.onboarding import service
from src.platform.onboarding.models import AccessRequestStatus
from src.platform.tenancy.models import Company
from src.platform.users.models import User
from src.platform.web.rate_limit import SlidingWindowLimiter

router = APIRouter(prefix="/api/access-requests", tags=["onboarding"])

# El alta de solicitudes es pública (sin sesión): se limita por IP para evitar
# spam de registros (OWASP: Insecure Design). En memoria del proceso; para un
# límite global tras varias réplicas habría que respaldarlo en Redis.
_limite_solicitudes = SlidingWindowLimiter(max_attempts=5, window_seconds=3600)
# Verificar RUC es más liviano (solo lectura) pero público: límite mayor para que
# un formulario legítimo pueda chequear varias veces, acotando la enumeración.
_limite_verificacion = SlidingWindowLimiter(max_attempts=30, window_seconds=3600)


def _chequear_limite(request: Request, limiter: SlidingWindowLimiter) -> None:
    ip = request.client.host if request.client else "desconocida"
    espera = limiter.blocked_for(f"ip:{ip}")
    if espera:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Demasiadas solicitudes en poco tiempo. Intenta en {espera} segundo(s).",
        )
    limiter.register(f"ip:{ip}")


class AccessRequestInput(BaseModel):
    email: EmailStr
    nombre: str = Field(min_length=1, max_length=150)
    # Opcional: solo se usa cuando el RUC es libre (razón social del primer
    # cliente). Si el RUC ya existe se ignora (la empresa ya tiene nombre).
    empresa_nombre: str | None = Field(default=None, max_length=200)
    ruc: str = Field(min_length=11, max_length=11)
    telefono: str | None = Field(default=None, max_length=20)
    mensaje: str | None = None


class VerificarRucResponse(BaseModel):
    existe: bool
    razon_social: str | None = None


class AccessRequestReview(BaseModel):
    status: AccessRequestStatus
    rejection_reason: str | None = None


class AccessRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    nombre: str
    empresa_nombre: str
    ruc: str
    status: AccessRequestStatus
    created_at: datetime
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None


class SolicitudPublicaResponse(BaseModel):
    # tipo: "estudio_nuevo" (RUC libre → SuperAdmin) | "acceso_empresa" (RUC existe
    # → admin dueño). El frontend muestra el mensaje adecuado según el tipo.
    tipo: str
    email: str
    ruc: str
    detalle: str


@router.post("", response_model=SolicitudPublicaResponse, status_code=status.HTTP_201_CREATED)
def create_access_request(
    payload: AccessRequestInput, request: Request, db: Session = Depends(get_db)
) -> SolicitudPublicaResponse:
    """Solicitud pública. Si el RUC es libre, pide alta de estudio (la revisa el
    SuperAdmin); si ya existe, pide acceso al admin dueño de ese estudio."""
    _chequear_limite(request, _limite_solicitudes)
    try:
        tipo, detalle = service.create_request(db, payload.model_dump())
    except service.OnboardingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    return SolicitudPublicaResponse(
        tipo=tipo, email=payload.email, ruc=payload.ruc, detalle=detalle
    )


@router.get("/verificar-ruc", response_model=VerificarRucResponse)
def verificar_ruc(
    ruc: str, request: Request, db: Session = Depends(get_db)
) -> VerificarRucResponse:
    """Público: dice si un RUC ya está registrado (y su razón social, dato público
    de SUNAT) para que el formulario guíe a registrar estudio o solicitar acceso."""
    _chequear_limite(request, _limite_verificacion)
    if not (len(ruc) == 11 and ruc.isdigit()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="RUC inválido")
    company = db.scalar(select(Company).where(Company.ruc == ruc))
    return VerificarRucResponse(
        existe=company is not None,
        razon_social=company.razon_social if company else None,
    )


@router.get("", response_model=list[AccessRequestResponse])
def list_access_requests(
    status_filter: AccessRequestStatus | None = None,
    _admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> list[AccessRequestResponse]:
    return [
        AccessRequestResponse.model_validate(r)
        for r in service.list_requests(db, status_filter)
    ]


@router.put("/{request_id}/review", response_model=AccessRequestResponse)
def review_access_request(
    request_id: int,
    payload: AccessRequestReview,
    admin: User = Depends(require_platform_admin),
    db: Session = Depends(get_db),
) -> AccessRequestResponse:
    """Aprueba (crea empresa + usuario Auth0 + membresía admin) o rechaza."""
    try:
        if payload.status == AccessRequestStatus.aprobado:
            req = service.approve_request(db, request_id, admin.id)
        else:
            req = service.reject_request(
                db, request_id, admin.id, payload.rejection_reason
            )
    except service.OnboardingError as exc:
        detalle = str(exc)
        codigo = (
            status.HTTP_404_NOT_FOUND
            if "no encontrada" in detalle
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(codigo, detail=detalle)
    return AccessRequestResponse.model_validate(req)
