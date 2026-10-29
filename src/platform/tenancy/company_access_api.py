"""
Solicitar acceso a una empresa que YA existe (su RUC está tomado).

Flujo: un usuario registrado pide acceso a un RUC ocupado → la solicitud le llega
al admin del estudio dueño → el admin la aprueba concediéndole una membresía con
el rol que elija (operador/asistente/consulta), o la rechaza. Reemplaza el muro
del 409 "ya registrado" por una vía de acceso controlada por el dueño.
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.models import Role, RoleScope
from src.platform.database.base import utcnow
from src.platform.database.session import get_db
from src.platform.identity import auth0
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import (
    Company,
    CompanyAccessRequest,
    CompanyAccessRequestStatus,
    EstudioMember,
    EstudioMemberStatus,
    Membership,
    MembershipStatus,
)
from src.platform.tenancy.service import (
    ADMIN_ROLE_KEY,
    ASSIGNABLE_ROLE_KEYS,
    estudio_admin_role_id,
    get_active_membership,
)
from src.platform.users.models import User, UserStatus

router = APIRouter(prefix="/api/company-access-requests", tags=["company-access"])


def _provisionar_usuario(db: Session, email: str, nombre: str) -> User:
    """Usuario para un solicitante PÚBLICO: reutiliza si el email ya existe, o lo
    crea en Auth0 (que envía el correo para fijar contraseña) y en la BD local."""
    existente = db.scalar(select(User).where(User.email == email))
    if existente is not None:
        return existente
    try:
        auth0_sub = auth0.crear_usuario(email, nombre, secrets.token_urlsafe(12) + "A1!")
        auth0.enviar_reset_password(email)
    except auth0.Auth0Error as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    usuario = User(
        email=email, nombre=nombre, auth0_sub=auth0_sub, status=UserStatus.activo
    )
    db.add(usuario)
    db.flush()
    return usuario


class AccessRequestInput(BaseModel):
    ruc: str = Field(min_length=11, max_length=11)
    mensaje: str | None = Field(default=None, max_length=500)


class AccessReviewInput(BaseModel):
    status: CompanyAccessRequestStatus
    role_key: str | None = None  # requerido al aprobar
    motivo: str | None = Field(default=None, max_length=500)


class AccessRequestItem(BaseModel):
    id: int
    ruc: str
    razon_social: str
    requester_email: str
    requester_nombre: str
    mensaje: str | None
    status: CompanyAccessRequestStatus


def _estudios_admin_ids(db: Session, user: User) -> list[int]:
    return list(
        db.scalars(
            select(EstudioMember.estudio_id)
            .join(Role, Role.id == EstudioMember.role_id)
            .where(
                EstudioMember.user_id == user.id,
                EstudioMember.status == EstudioMemberStatus.activo,
                Role.key == ADMIN_ROLE_KEY,
            )
        ).all()
    )


@router.post("", response_model=AccessRequestItem, status_code=status.HTTP_201_CREATED)
def solicitar_acceso(
    payload: AccessRequestInput,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccessRequestItem:
    company = db.scalar(select(Company).where(Company.ruc == payload.ruc))
    if company is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="Ese RUC no está registrado; puedes crear la empresa tú mismo",
        )
    # Si ya tiene acceso (membresía directa o es admin del estudio dueño), no aplica.
    if get_active_membership(db, user.id, company.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ya tienes acceso a esa empresa")
    if estudio_admin_role_id(db, user.id, company.estudio_id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Ya administras esa empresa")

    pendiente = db.scalar(
        select(CompanyAccessRequest).where(
            CompanyAccessRequest.requester_user_id == user.id,
            CompanyAccessRequest.company_id == company.id,
            CompanyAccessRequest.status == CompanyAccessRequestStatus.pendiente,
        )
    )
    if pendiente is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Ya tienes una solicitud pendiente para esa empresa"
        )

    solicitud = CompanyAccessRequest(
        requester_user_id=user.id, company_id=company.id, mensaje=payload.mensaje
    )
    db.add(solicitud)
    db.commit()
    return AccessRequestItem(
        id=solicitud.id, ruc=company.ruc, razon_social=company.razon_social,
        requester_email=user.email, requester_nombre=user.nombre,
        mensaje=solicitud.mensaje, status=solicitud.status,
    )


@router.get("", response_model=list[AccessRequestItem])
def list_pendientes(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[AccessRequestItem]:
    """Bandeja del admin: solicitudes pendientes de empresas de sus estudios."""
    estudios = _estudios_admin_ids(db, user)
    if not estudios:
        return []
    filas = db.execute(
        select(CompanyAccessRequest, Company, User)
        .join(Company, Company.id == CompanyAccessRequest.company_id)
        .outerjoin(User, User.id == CompanyAccessRequest.requester_user_id)
        .where(
            Company.estudio_id.in_(estudios),
            CompanyAccessRequest.status == CompanyAccessRequestStatus.pendiente,
        )
        .order_by(CompanyAccessRequest.created_at.desc())
    ).all()
    return [
        AccessRequestItem(
            id=s.id, ruc=c.ruc, razon_social=c.razon_social,
            # Público (sin cuenta aún): usa los datos guardados en la solicitud.
            requester_email=u.email if u else (s.requester_email or ""),
            requester_nombre=u.nombre if u else (s.requester_nombre or ""),
            mensaje=s.mensaje, status=s.status,
        )
        for s, c, u in filas
    ]


@router.put("/{request_id}/review", response_model=AccessRequestItem)
def revisar(
    request_id: int,
    payload: AccessReviewInput,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AccessRequestItem:
    solicitud = db.get(CompanyAccessRequest, request_id)
    if solicitud is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Solicitud no encontrada")
    if solicitud.status != CompanyAccessRequestStatus.pendiente:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="La solicitud ya fue revisada")

    company = db.get(Company, solicitud.company_id)
    # Solo un admin del estudio dueño puede revisarla.
    if estudio_admin_role_id(db, user.id, company.estudio_id) is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="No administras el estudio dueño de esa empresa"
        )

    requester = (
        db.get(User, solicitud.requester_user_id)
        if solicitud.requester_user_id is not None
        else None
    )
    if payload.status == CompanyAccessRequestStatus.aprobado:
        if not payload.role_key:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="Indica el rol a conceder (role_key)"
            )
        if payload.role_key not in ASSIGNABLE_ROLE_KEYS:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                detail="Rol inválido (usa operador, asistente o consulta)",
            )
        role = db.scalar(select(Role).where(Role.key == payload.role_key))
        if role is None or role.scope != RoleScope.company:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Rol inválido")
        # Solicitud pública: recién al aprobar se provisiona la cuenta.
        if requester is None:
            requester = _provisionar_usuario(
                db,
                solicitud.requester_email,
                solicitud.requester_nombre or solicitud.requester_email,
            )
            solicitud.requester_user_id = requester.id
        ya = db.scalar(
            select(Membership).where(
                Membership.user_id == requester.id,
                Membership.company_id == company.id,
            )
        )
        if ya is None:
            db.add(
                Membership(
                    user_id=requester.id, company_id=company.id, role_id=role.id,
                    status=MembershipStatus.activo,
                )
            )
        else:
            ya.role_id = role.id
            ya.status = MembershipStatus.activo
        solicitud.role_id = role.id
    elif payload.status == CompanyAccessRequestStatus.rechazado:
        solicitud.rejection_reason = payload.motivo
    else:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Estado inválido")

    solicitud.status = payload.status
    solicitud.reviewed_by_id = user.id
    solicitud.reviewed_at = utcnow()
    db.commit()
    return AccessRequestItem(
        id=solicitud.id, ruc=company.ruc, razon_social=company.razon_social,
        requester_email=requester.email if requester else (solicitud.requester_email or ""),
        requester_nombre=requester.nombre if requester else (solicitud.requester_nombre or ""),
        mensaje=solicitud.mensaje, status=solicitud.status,
    )
