"""Consultas de tenencia. Toda operación se filtra siempre por empresa activa."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.tenancy.models import Membership, MembershipStatus


def get_active_membership(db: Session, user_id: int, company_id: int) -> Membership | None:
    """Devuelve la membresía activa del usuario en la empresa, o None."""
    return db.scalar(
        select(Membership).where(
            Membership.user_id == user_id,
            Membership.company_id == company_id,
            Membership.status == MembershipStatus.activo,
        )
    )
