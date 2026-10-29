"""
Endpoint de contexto del usuario autenticado.

`GET /me` devuelve el usuario y las empresas a las que pertenece (Modelo B) —
lo que el frontend necesita para pintar el selector de empresa y el menú.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.entitlements import active_module_keys
from src.platform.authorization.models import Role
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import (
    Company,
    EstudioMember,
    EstudioMemberStatus,
    Membership,
    MembershipStatus,
)
from src.platform.tenancy.service import ADMIN_ROLE_KEY
from src.platform.users.models import User

router = APIRouter(tags=["me"])


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    # 1) Empresas con membresía directa (acceso por empresa).
    visibles: dict[int, tuple[Company, str]] = {
        company.id: (company, role_key)
        for company, role_key in db.execute(
            select(Company, Role.key)
            .join(Membership, Membership.company_id == Company.id)
            .join(Role, Role.id == Membership.role_id)
            .where(
                Membership.user_id == user.id,
                Membership.status == MembershipStatus.activo,
            )
        ).all()
    }

    # 2) Admin de estudio → TODAS las empresas de esos estudios (prevalece admin).
    estudios_admin = db.scalars(
        select(EstudioMember.estudio_id)
        .join(Role, Role.id == EstudioMember.role_id)
        .where(
            EstudioMember.user_id == user.id,
            EstudioMember.status == EstudioMemberStatus.activo,
            Role.key == ADMIN_ROLE_KEY,
        )
    ).all()
    if estudios_admin:
        for company in db.scalars(
            select(Company).where(Company.estudio_id.in_(estudios_admin))
        ).all():
            visibles[company.id] = (company, ADMIN_ROLE_KEY)

    rows = list(visibles.values())
    modulos = active_module_keys(db, [company.id for company, _ in rows])
    return {
        "id": user.id,
        "email": user.email,
        "nombre": user.nombre,
        "is_platform_admin": user.is_platform_admin,
        "companies": [
            {
                "id": company.id,
                "ruc": company.ruc,
                "razon_social": company.razon_social,
                "role_key": role_key,
                "modules": modulos.get(company.id, []),
            }
            for company, role_key in rows
        ],
    }
