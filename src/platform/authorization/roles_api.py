"""Listado de roles de empresa (para asignarlos al invitar/editar miembros)."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.models import Role, RoleScope
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.users.models import User

router = APIRouter(prefix="/api/roles", tags=["roles"])


class RoleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    key: str
    nombre: str


@router.get("", response_model=list[RoleResponse])
def list_company_roles(
    _user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[RoleResponse]:
    rows = db.scalars(
        select(Role).where(Role.scope == RoleScope.company).order_by(Role.nombre)
    ).all()
    return [RoleResponse.model_validate(r) for r in rows]
