"""
Gestión del estudio y sus admins (modelo de estudios).

Un admin de estudio ve su(s) estudio(s) y su equipo, y puede sumar o quitar
co-admins (otras personas que, como él, administran toda la cartera). Los
miembros no-admin con acceso acotado a ciertos clientes se gestionan por empresa
en `team_api`/`members_api`; aquí solo viven los admins del estudio.
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.models import Role
from src.platform.database.session import get_db
from src.platform.identity import auth0
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import (
    Estudio,
    EstudioMember,
    EstudioMemberStatus,
)
from src.platform.tenancy.service import ADMIN_ROLE_KEY
from src.platform.users.models import User, UserStatus

router = APIRouter(prefix="/api/estudio", tags=["estudio"])


class EstudioMemberItem(BaseModel):
    user_id: int
    email: str
    nombre: str
    role_key: str
    status: EstudioMemberStatus


class EstudioItem(BaseModel):
    id: int
    nombre: str
    members: list[EstudioMemberItem]


class AdminInvite(BaseModel):
    email: EmailStr
    nombre: str = Field(min_length=1, max_length=150)
    estudio_id: int | None = None  # requerido solo si administras más de un estudio


class _MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    email: str
    nombre: str
    role_key: str
    status: EstudioMemberStatus


def _estudios_admin(db: Session, user: User) -> list[Estudio]:
    """Estudios donde el usuario es admin ACTIVO (los que puede administrar)."""
    ids = db.scalars(
        select(EstudioMember.estudio_id)
        .join(Role, Role.id == EstudioMember.role_id)
        .where(
            EstudioMember.user_id == user.id,
            EstudioMember.status == EstudioMemberStatus.activo,
            Role.key == ADMIN_ROLE_KEY,
        )
    ).all()
    if not ids:
        return []
    return list(db.scalars(select(Estudio).where(Estudio.id.in_(ids))).all())


def _resolver_estudio(db: Session, user: User, estudio_id: int | None) -> Estudio:
    """El estudio sobre el que opera el admin: el único que administra, o el
    indicado (validando que lo administre)."""
    admin = _estudios_admin(db, user)
    if not admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="No eres admin de ningún estudio"
        )
    if estudio_id is not None:
        elegido = next((e for e in admin if e.id == estudio_id), None)
        if elegido is None:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="No administras ese estudio"
            )
        return elegido
    if len(admin) > 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Indica el estudio (estudio_id)"
        )
    return admin[0]


def _miembros(db: Session, estudio_id: int) -> list[EstudioMemberItem]:
    filas = db.execute(
        select(EstudioMember, User, Role)
        .join(User, User.id == EstudioMember.user_id)
        .join(Role, Role.id == EstudioMember.role_id)
        .where(EstudioMember.estudio_id == estudio_id)
    ).all()
    return [
        EstudioMemberItem(
            user_id=u.id, email=u.email, nombre=u.nombre,
            role_key=r.key, status=m.status,
        )
        for m, u, r in filas
    ]


@router.get("", response_model=list[EstudioItem])
def list_mis_estudios(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[EstudioItem]:
    return [
        EstudioItem(id=e.id, nombre=e.nombre, members=_miembros(db, e.id))
        for e in _estudios_admin(db, user)
    ]


@router.post("/admins", response_model=_MemberResponse, status_code=status.HTTP_201_CREATED)
def invite_admin(
    payload: AdminInvite,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> _MemberResponse:
    estudio = _resolver_estudio(db, user, payload.estudio_id)
    admin_role = db.scalar(select(Role).where(Role.key == ADMIN_ROLE_KEY))
    if admin_role is None:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Falta el rol 'admin_empresa' (ejecuta el seed)",
        )

    # Una sola cuenta Auth0 por persona: si el email existe se reutiliza.
    invitado = db.scalar(select(User).where(User.email == payload.email))
    if invitado is None:
        try:
            auth0_sub = auth0.crear_usuario(
                payload.email, payload.nombre, secrets.token_urlsafe(12) + "A1!"
            )
            auth0.enviar_reset_password(payload.email)
        except auth0.Auth0Error as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc))
        invitado = User(
            email=payload.email,
            nombre=payload.nombre,
            auth0_sub=auth0_sub,
            status=UserStatus.activo,
        )
        db.add(invitado)
        db.flush()

    # Upsert de la membresía de estudio (reactiva si estaba inactiva).
    miembro = db.scalar(
        select(EstudioMember).where(
            EstudioMember.estudio_id == estudio.id,
            EstudioMember.user_id == invitado.id,
        )
    )
    if miembro is None:
        miembro = EstudioMember(
            estudio_id=estudio.id, user_id=invitado.id, role_id=admin_role.id
        )
        db.add(miembro)
    else:
        miembro.role_id = admin_role.id
        miembro.status = EstudioMemberStatus.activo
    db.commit()

    return _MemberResponse(
        user_id=invitado.id, email=invitado.email, nombre=invitado.nombre,
        role_key=ADMIN_ROLE_KEY, status=EstudioMemberStatus.activo,
    )


@router.delete("/admins/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_admin(
    user_id: int,
    estudio_id: int | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    estudio = _resolver_estudio(db, user, estudio_id)
    if user_id == user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="No puedes quitarte a ti mismo"
        )
    miembro = db.scalar(
        select(EstudioMember).where(
            EstudioMember.estudio_id == estudio.id,
            EstudioMember.user_id == user_id,
        )
    )
    if miembro is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Miembro no encontrado")
    miembro.status = EstudioMemberStatus.inactivo
    db.commit()
