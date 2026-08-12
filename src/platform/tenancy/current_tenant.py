"""
Dependencia de FastAPI: resuelve la EMPRESA ACTIVA del request y valida que el
usuario sea miembro de ella (defensa #1 contra fuga entre tenants).

La empresa activa se indica con la cabecera `X-Company-Id`. Sin membresía
válida → 403, sin importar lo que el cliente envíe.
"""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import Company, Membership
from src.platform.tenancy.service import get_active_membership
from src.platform.users.models import User


@dataclass
class ActiveContext:
    """Contexto resuelto de un request: usuario + empresa activa + su membresía."""

    user: User
    company: Company
    membership: Membership


def get_active_context(
    x_company_id: int | None = Header(default=None, alias="X-Company-Id"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActiveContext:
    if x_company_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Falta la cabecera X-Company-Id"
        )

    membership = get_active_membership(db, user.id, x_company_id)
    if membership is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="No pertenece a la empresa indicada"
        )

    company = db.get(Company, x_company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Empresa no encontrada")

    return ActiveContext(user=user, company=company, membership=membership)
