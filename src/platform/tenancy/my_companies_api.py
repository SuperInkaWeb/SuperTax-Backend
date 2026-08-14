"""
Alta de empresas self-service (Modelo B).

Un usuario autenticado crea una empresa nueva y queda como su Admin. La empresa
nace SIN módulos: activarlos es acción del SuperAdmin, que así conserva el control
de acceso (y la palanca de cobro). Pensado para el contador que agrega sus RUCs.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.models import Role
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import Company, Membership
from src.platform.users.models import User

router = APIRouter(prefix="/api/my-companies", tags=["my-companies"])

_ADMIN_ROLE_KEY = "admin_empresa"


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

    company = Company(ruc=payload.ruc, razon_social=payload.razon_social)
    db.add(company)
    db.flush()
    db.add(Membership(user_id=user.id, company_id=company.id, role_id=admin_role.id))
    db.commit()
    db.refresh(company)
    return MyCompanyResponse.model_validate(company)
