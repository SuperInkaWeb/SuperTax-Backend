"""Motor de permisos (RBAC). ¿El rol dado tiene el permiso dado?"""
from sqlalchemy.orm import Session

from src.platform.authorization.models import Role


def role_has_permission(db: Session, role_id: int, permission_key: str) -> bool:
    role = db.get(Role, role_id)
    if role is None:
        return False
    return any(p.key == permission_key for p in role.permissions)
