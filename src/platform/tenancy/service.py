"""Consultas de tenencia. Toda operación se filtra siempre por empresa activa."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.models import Role
from src.platform.tenancy.models import (
    EstudioMember,
    EstudioMemberStatus,
    Membership,
    MembershipStatus,
)

# Rol que, a nivel estudio, otorga acceso total a todas sus empresas.
ADMIN_ROLE_KEY = "admin_empresa"

# Roles que se asignan por empresa (staff acotado). Admin NO va aquí: un admin se
# gestiona a nivel estudio (estudio_members), nunca por empresa.
ASSIGNABLE_ROLE_KEYS = ("operador", "asistente", "consulta")


def get_active_membership(db: Session, user_id: int, company_id: int) -> Membership | None:
    """Devuelve la membresía activa del usuario en la empresa, o None."""
    return db.scalar(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.company_id == company_id,
            Membership.status == MembershipStatus.activo,
        )
    )


def estudio_admin_role_id(db: Session, user_id: int, estudio_id: int | None) -> int | None:
    """Rol de admin si el usuario es admin activo del estudio dado; si no, None.

    Es la vía por la que un admin de estudio ve y opera TODAS las empresas del
    estudio sin necesitar una membresía por empresa."""
    if estudio_id is None:
        return None
    return db.scalar(
        select(EstudioMember.role_id)
        .join(Role, Role.id == EstudioMember.role_id)
        .where(
            EstudioMember.user_id == user_id,
            EstudioMember.estudio_id == estudio_id,
            EstudioMember.status == EstudioMemberStatus.activo,
            Role.key == ADMIN_ROLE_KEY,
        )
    )
