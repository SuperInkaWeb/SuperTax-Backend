"""
Dependencia de FastAPI: resuelve el usuario autenticado desde el token Auth0.

Es el primer eslabón del pipeline de seguridad:
    get_current_user → get_active_context → require_module / require_permission
"""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.database.session import get_db
from src.platform.identity.auth0 import Auth0Error, validar_token
from src.platform.users.models import User, UserStatus

_bearer = HTTPBearer()


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    try:
        claims = validar_token(creds.credentials)
    except Auth0Error as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=str(e))

    user = db.scalar(select(User).where(User.auth0_sub == claims.get("sub")))
    if user is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Usuario no registrado en la plataforma"
        )
    if user.status != UserStatus.activo:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Usuario inactivo")
    return user
