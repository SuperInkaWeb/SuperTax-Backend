"""
Casos de uso del onboarding.

Aprobar una solicitud crea, en una sola operación: la empresa, el usuario en
Auth0 (que recibe el email para establecer su contraseña), el usuario local y su
membresía como Admin de la empresa (Modelo B).
"""
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.models import Role
from src.platform.database.base import utcnow
from src.platform.identity import auth0
from src.platform.onboarding.models import AccessRequest, AccessRequestStatus
from src.platform.tenancy.models import Company, Membership
from src.platform.users.models import User, UserStatus

ADMIN_ROLE_KEY = "admin_empresa"


class OnboardingError(Exception):
    """Error de negocio del onboarding (se traduce a 4xx en la API)."""


def create_request(db: Session, data: dict) -> AccessRequest:
    pendiente = db.scalar(
        select(AccessRequest).where(
            AccessRequest.email == data["email"],
            AccessRequest.status == AccessRequestStatus.pendiente,
        )
    )
    if pendiente is not None:
        raise OnboardingError("Ya existe una solicitud pendiente con ese email")
    request = AccessRequest(**data)
    db.add(request)
    db.commit()
    db.refresh(request)
    return request


def list_requests(
    db: Session, status_filter: AccessRequestStatus | None = None
) -> list[AccessRequest]:
    query = select(AccessRequest)
    if status_filter is not None:
        query = query.where(AccessRequest.status == status_filter)
    return list(db.scalars(query.order_by(AccessRequest.created_at.desc())).all())


def _get(db: Session, request_id: int) -> AccessRequest:
    req = db.get(AccessRequest, request_id)
    if req is None:
        raise OnboardingError("Solicitud no encontrada")
    if req.status != AccessRequestStatus.pendiente:
        raise OnboardingError("La solicitud ya fue revisada")
    return req


def reject_request(
    db: Session, request_id: int, reviewer_id: int, reason: str | None
) -> AccessRequest:
    req = _get(db, request_id)
    req.status = AccessRequestStatus.rechazado
    req.reviewed_by_id = reviewer_id
    req.reviewed_at = utcnow()
    req.rejection_reason = reason
    db.commit()
    db.refresh(req)
    return req


def approve_request(db: Session, request_id: int, reviewer_id: int) -> AccessRequest:
    req = _get(db, request_id)

    if db.scalar(select(Company).where(Company.ruc == req.ruc)) is not None:
        raise OnboardingError("El RUC ya está registrado en el sistema")
    admin_role = db.scalar(select(Role).where(Role.key == ADMIN_ROLE_KEY))
    if admin_role is None:
        raise OnboardingError("Falta el rol base 'admin_empresa' (ejecuta el seed)")

    company = Company(ruc=req.ruc, razon_social=req.empresa_nombre)
    db.add(company)
    db.flush()

    # Auth0 gestiona la identidad y la contraseña (envía el email para fijarla).
    try:
        auth0_sub = auth0.crear_usuario(
            req.email, req.nombre, secrets.token_urlsafe(12) + "A1!"
        )
        auth0.enviar_reset_password(req.email)
    except auth0.Auth0Error as exc:
        raise OnboardingError(str(exc))

    user = User(
        email=req.email,
        nombre=req.nombre,
        auth0_sub=auth0_sub,
        status=UserStatus.activo,
    )
    db.add(user)
    db.flush()
    db.add(Membership(user_id=user.id, company_id=company.id, role_id=admin_role.id))

    req.status = AccessRequestStatus.aprobado
    req.reviewed_by_id = reviewer_id
    req.reviewed_at = utcnow()
    db.commit()
    db.refresh(req)
    return req
