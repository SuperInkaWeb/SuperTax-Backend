"""
Endpoints de miembros de la empresa activa (Modelo B).

El Admin de la empresa invita usuarios (se crean en Auth0 si no existen y se les
envía el email para fijar contraseña), les asigna un rol por empresa, lo cambia o
los remueve. Todo requiere el permiso `company.member.manage` y se filtra por la
empresa activa.
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.deps import require_permission
from src.platform.authorization.models import Role, RoleScope
from src.platform.database.session import get_db
from src.platform.identity import auth0
from src.platform.tenancy.current_tenant import ActiveContext
from src.platform.tenancy.models import Membership, MembershipStatus
from src.platform.users.models import User, UserStatus

router = APIRouter(prefix="/api/members", tags=["members"])

_MANAGE = "company.member.manage"


class MemberInvite(BaseModel):
    email: EmailStr
    nombre: str = Field(min_length=1, max_length=150)
    role_key: str


class RoleChange(BaseModel):
    role_key: str


class MemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    membership_id: int
    user_id: int
    email: str
    nombre: str
    role_key: str
    status: MembershipStatus


def _rol_de_empresa(db: Session, role_key: str) -> Role:
    role = db.scalar(select(Role).where(Role.key == role_key))
    if role is None or role.scope != RoleScope.company:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Rol inválido")
    return role


def _serializar(db: Session, membership: Membership) -> MemberResponse:
    user = db.get(User, membership.user_id)
    role = db.get(Role, membership.role_id)
    return MemberResponse(
        membership_id=membership.id,
        user_id=user.id,
        email=user.email,
        nombre=user.nombre,
        role_key=role.key,
        status=membership.status,
    )


@router.get("", response_model=list[MemberResponse])
def list_members(
    ctx: ActiveContext = Depends(require_permission(_MANAGE)),
    db: Session = Depends(get_db),
) -> list[MemberResponse]:
    rows = db.scalars(
        select(Membership).where(Membership.company_id == ctx.company.id)
    ).all()
    return [_serializar(db, m) for m in rows]


@router.post("", response_model=MemberResponse, status_code=status.HTTP_201_CREATED)
def invite_member(
    payload: MemberInvite,
    ctx: ActiveContext = Depends(require_permission(_MANAGE)),
    db: Session = Depends(get_db),
) -> MemberResponse:
    role = _rol_de_empresa(db, payload.role_key)

    user = db.scalar(select(User).where(User.email == payload.email))
    if user is None:
        try:
            auth0_sub = auth0.crear_usuario(
                payload.email, payload.nombre, secrets.token_urlsafe(12) + "A1!"
            )
            auth0.enviar_reset_password(payload.email)
        except auth0.Auth0Error as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc))
        user = User(
            email=payload.email,
            nombre=payload.nombre,
            auth0_sub=auth0_sub,
            status=UserStatus.activo,
        )
        db.add(user)
        db.flush()

    ya_miembro = db.scalar(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.company_id == ctx.company.id,
        )
    )
    if ya_miembro is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="El usuario ya pertenece a la empresa"
        )

    membership = Membership(
        user_id=user.id, company_id=ctx.company.id, role_id=role.id
    )
    db.add(membership)
    db.commit()
    db.refresh(membership)
    return _serializar(db, membership)


def _membership_de_empresa(db: Session, membership_id: int, company_id: int) -> Membership:
    membership = db.get(Membership, membership_id)
    if membership is None or membership.company_id != company_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Miembro no encontrado")
    return membership


@router.put("/{membership_id}", response_model=MemberResponse)
def change_role(
    membership_id: int,
    payload: RoleChange,
    ctx: ActiveContext = Depends(require_permission(_MANAGE)),
    db: Session = Depends(get_db),
) -> MemberResponse:
    membership = _membership_de_empresa(db, membership_id, ctx.company.id)
    membership.role_id = _rol_de_empresa(db, payload.role_key).id
    db.commit()
    db.refresh(membership)
    return _serializar(db, membership)


@router.delete("/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    membership_id: int,
    ctx: ActiveContext = Depends(require_permission(_MANAGE)),
    db: Session = Depends(get_db),
) -> None:
    membership = _membership_de_empresa(db, membership_id, ctx.company.id)
    if membership.user_id == ctx.user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="No puedes removerte a ti mismo"
        )
    db.delete(membership)
    db.commit()
