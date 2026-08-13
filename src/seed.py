"""
Seed idempotente de la plataforma.

Crea los roles base de empresa y, a partir de los descriptores de módulos,
registra cada módulo y sus permisos, asignándolos a los roles según una política
por defecto. Se puede correr varias veces sin duplicar.

    python -m src.seed

Vive a nivel de `src` (no dentro de `platform/`) porque lee el registro de
módulos; el núcleo nunca debe depender de los módulos.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.module_registry import MODULES
from src.platform.authorization.models import Module, Permission, Role, RoleScope
from src.platform.database.session import SessionLocal

BASE_ROLES = {
    "admin_empresa": "Admin Empresa",
    "supervisor": "Supervisor",
    "operador": "Operador",
    "consulta": "Consulta",
}

# Permisos de administración de la empresa (Core), solo para el Admin de empresa.
CORE_COMPANY_PERMISSIONS = ("company.member.manage", "company.manage")


def _rol_recibe(role_key: str, permission_key: str) -> bool:
    """Política por defecto de asignación permiso→rol."""
    if role_key in ("admin_empresa", "supervisor"):
        return True
    if role_key == "operador":
        return permission_key.endswith((".read", ".create"))
    if role_key == "consulta":
        return permission_key.endswith(".read")
    return False


def _upsert_role(db: Session, key: str, nombre: str) -> Role:
    role = db.scalar(select(Role).where(Role.key == key))
    if role is None:
        role = Role(key=key, nombre=nombre, scope=RoleScope.company)
        db.add(role)
        db.flush()
    return role


def _upsert_permission(db: Session, key: str) -> Permission:
    perm = db.scalar(select(Permission).where(Permission.key == key))
    if perm is None:
        perm = Permission(key=key)
        db.add(perm)
        db.flush()
    return perm


def _upsert_module(db: Session, key: str, nombre: str) -> None:
    if db.scalar(select(Module).where(Module.key == key)) is None:
        db.add(Module(key=key, nombre=nombre))


def seed(db: Session) -> None:
    roles = {key: _upsert_role(db, key, nombre) for key, nombre in BASE_ROLES.items()}

    # Permisos de administración de empresa → solo el Admin de la empresa.
    admin_empresa = roles["admin_empresa"]
    for permission_key in CORE_COMPANY_PERMISSIONS:
        permission = _upsert_permission(db, permission_key)
        if permission not in admin_empresa.permissions:
            admin_empresa.permissions.append(permission)

    for descriptor in MODULES:
        _upsert_module(db, descriptor.key, descriptor.name)
        for permission_key in descriptor.permissions:
            permission = _upsert_permission(db, permission_key)
            for role in roles.values():
                if _rol_recibe(role.key, permission_key) and permission not in role.permissions:
                    role.permissions.append(permission)
    db.commit()


def main() -> None:
    db = SessionLocal()
    try:
        seed(db)
        print("Seed completado: roles base + módulos y permisos registrados.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
