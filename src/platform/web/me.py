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
from src.platform.tenancy.models import Company, Membership, MembershipStatus
from src.platform.users.models import User

router = APIRouter(tags=["me"])


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    rows = db.execute(
        select(Company, Role.key)
        .join(Membership, Membership.company_id == Company.id)
        .join(Role, Role.id == Membership.role_id)
        .where(
            Membership.user_id == user.id,
            Membership.status == MembershipStatus.activo,
        )
    ).all()

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
