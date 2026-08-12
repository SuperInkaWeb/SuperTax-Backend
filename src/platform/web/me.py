"""
Endpoint de contexto del usuario autenticado.

`GET /me` devuelve el usuario y las empresas a las que pertenece (Modelo B) —
lo que el frontend necesita para pintar el selector de empresa y el menú.
"""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import Company, Membership
from src.platform.users.models import User

router = APIRouter(tags=["me"])


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    companies = (
        db.execute(
            select(Company)
            .join(Membership, Membership.company_id == Company.id)
            .where(Membership.user_id == user.id)
        )
        .scalars()
        .all()
    )
    return {
        "id": user.id,
        "email": user.email,
        "nombre": user.nombre,
        "is_platform_admin": user.is_platform_admin,
        "companies": [
            {"id": c.id, "ruc": c.ruc, "razon_social": c.razon_social} for c in companies
        ],
    }
