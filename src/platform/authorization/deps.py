"""
Guardas de autorización para los routers de los módulos (la "doble puerta").

Uso en un endpoint de módulo (Fases 2-4):

    @router.post("/jobs", dependencies=[Depends(require_module("sire"))])
    def crear_job(ctx: ActiveContext = Depends(require_permission("sire.job.create"))):
        ...

`require_module` valida el entitlement (¿empresa contrató el módulo?).
`require_permission` valida el permiso del rol (¿este usuario puede?).
Ambas devuelven el `ActiveContext` ya resuelto.
"""
from collections.abc import Callable

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.platform.authorization.entitlements import company_has_module
from src.platform.authorization.permissions import role_has_permission
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.current_tenant import ActiveContext, get_active_context
from src.platform.users.models import User


def require_platform_admin(user: User = Depends(get_current_user)) -> User:
    """Gate de acciones de plataforma (SuperAdmin): crear empresas, aprobar accesos…"""
    if not user.is_platform_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Requiere permisos de plataforma"
        )
    return user


def require_module(module_key: str) -> Callable[..., ActiveContext]:
    def dependency(
        ctx: ActiveContext = Depends(get_active_context),
        db: Session = Depends(get_db),
    ) -> ActiveContext:
        if not company_has_module(db, ctx.company.id, module_key):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=f"El módulo '{module_key}' no está contratado por la empresa",
            )
        return ctx

    return dependency


def require_permission(permission_key: str) -> Callable[..., ActiveContext]:
    def dependency(
        ctx: ActiveContext = Depends(get_active_context),
        db: Session = Depends(get_db),
    ) -> ActiveContext:
        if not role_has_permission(db, ctx.membership.role_id, permission_key):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=f"No tiene el permiso '{permission_key}'",
            )
        return ctx

    return dependency
