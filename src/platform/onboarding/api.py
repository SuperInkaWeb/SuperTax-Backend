"""Endpoints de onboarding (solicitudes de acceso)."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.orm import Session

from src.platform.authorization.deps import require_platform_admin
from src.platform.database.session import get_db
from src.platform.onboarding import service
from src.platform.onboarding.models import AccessRequestStatus
from src.platform.users.models import User

router = APIRouter(prefix="/api/access-requests", tags=["onboarding"])


class AccessRequestInput(BaseModel):
    email: EmailStr
    nombre: str = Field(min_length=1, max_length=150)
    empresa_nombre: str = Field(min_length=1, max_length=200)
    ruc: str = Field(min_length=11, max_length=11)
    telefono: str | None = Field(default=None, max_length=20)
    mensaje: str | None = None


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


@router.post("", response_model=AccessRequestResponse, status_code=status.HTTP_201_CREATED)
def create_access_request(
    payload: AccessRequestInput, db: Session = Depends(get_db)
) -> AccessRequestResponse:
    """Solicitud pública: un prospecto pide acceso a la plataforma."""
    try:
        req = service.create_request(db, payload.model_dump())
    except service.OnboardingError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))
    return AccessRequestResponse.model_validate(req)


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
