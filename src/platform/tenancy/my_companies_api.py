"""
Alta de empresas self-service.

Un usuario autenticado crea una empresa nueva en su estudio y queda como admin
del estudio (ve toda su cartera); no se le crea una membresía por empresa, que
sería redundante. La empresa nace con todos los módulos (provisional).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.entitlements import activar_todos_los_modulos
from src.platform.authorization.models import Role
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import (
    Company,
    Estudio,
    EstudioMember,
    EstudioMemberStatus,
)
from src.platform.users.models import User

router = APIRouter(prefix="/api/my-companies", tags=["my-companies"])

_ADMIN_ROLE_KEY = "admin_empresa"


def _estudio_del_usuario(db: Session, user: User, admin_role_id: int) -> Estudio:
    """Estudio propio del usuario; lo crea si no tiene y lo deja como admin del
    estudio (así ve toda su cartera sin membresías por empresa)."""
    estudio = db.scalar(select(Estudio).where(Estudio.owner_user_id == user.id))
    if estudio is None:
        estudio = Estudio(nombre=f"Estudio {user.nombre}", owner_user_id=user.id)
        db.add(estudio)
        db.flush()
    miembro = db.scalar(
        select(EstudioMember).where(
            EstudioMember.estudio_id == estudio.id,
            EstudioMember.user_id == user.id,
        )
    )
    if miembro is None:
        db.add(
            EstudioMember(
                estudio_id=estudio.id, user_id=user.id, role_id=admin_role_id
            )
        )
    elif miembro.status != EstudioMemberStatus.activo:
        miembro.status = EstudioMemberStatus.activo
    return estudio


class MyCompanyInput(BaseModel):
    ruc: str = Field(min_length=11, max_length=11)
    razon_social: str = Field(min_length=1, max_length=200)


class MyCompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ruc: str
    razon_social: str


@router.post("", response_model=MyCompanyResponse, status_code=status.HTTP_201_CREATED)
def create_my_company(
    payload: MyCompanyInput,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> MyCompanyResponse:
    if not payload.ruc.isdigit():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="El RUC debe tener 11 dígitos numéricos"
        )
    # RUC único: no se puede "reclamar" una empresa que ya existe (aislamiento).
    if db.scalar(select(Company).where(Company.ruc == payload.ruc)) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Ese RUC ya está registrado en la plataforma"
        )
    admin_role = db.scalar(select(Role).where(Role.key == _ADMIN_ROLE_KEY))
    if admin_role is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falta el rol base 'admin_empresa' (ejecuta el seed)",
        )

    estudio = _estudio_del_usuario(db, user, admin_role.id)
    company = Company(
        ruc=payload.ruc, razon_social=payload.razon_social, estudio_id=estudio.id
    )
    db.add(company)
    db.flush()
    activar_todos_los_modulos(db, company.id)
    db.commit()
    db.refresh(company)
    return MyCompanyResponse.model_validate(company)
