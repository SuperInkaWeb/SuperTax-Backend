"""
Dependencia de FastAPI: resuelve la EMPRESA ACTIVA del request y valida que el
usuario pueda acceder a ella (defensa #1 contra fuga entre tenants).

La empresa activa se indica con la cabecera `X-Company-Id`. El acceso se concede
por membresía directa en la empresa O por ser admin del estudio dueño (acceso
total a su cartera). Sin ninguna de las dos → 403.
"""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import Company
from src.platform.tenancy.service import estudio_admin_role_id, get_active_membership
from src.platform.users.models import User


@dataclass
class ActiveContext:
    """Contexto resuelto de un request: usuario + empresa activa + rol efectivo."""

    user: User
    company: Company
    role_id: int


def get_active_context(
    x_company_id: int | None = Header(default=None, alias="X-Company-Id"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ActiveContext:
    if x_company_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Falta la cabecera X-Company-Id"
        )

    company = db.get(Company, x_company_id)
    if company is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Empresa no encontrada")

    # Un admin del estudio dueño siempre tiene acceso admin, por encima de
    # cualquier membresía directa de menor rol (un admin es admin en toda su cartera).
    role_id = estudio_admin_role_id(db, user.id, company.estudio_id)
    if role_id is None:
        membership = get_active_membership(db, user.id, x_company_id)
        if membership is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="No pertenece a la empresa indicada"
            )
        role_id = membership.role_id

    return ActiveContext(user=user, company=company, role_id=role_id)
