"""
Gestión de equipo multi-empresa (Modelo B).

Un usuario que administra varias empresas (varios clientes de un estudio) gestiona
su equipo desde un solo lugar, sin depender de la empresa activa:

- Lista las empresas que administra.
- Asigna a una persona a varios clientes de una sola vez: se crea (o reutiliza)
  UNA cuenta Auth0 por email y UN `Membership` por cada cliente seleccionado.
- Activa/desactiva el acceso de una membresía (soft: no la borra).

Todo se valida contra las empresas que el propio usuario administra (permiso
`company.member.manage`), nunca contra lo que el cliente envíe.
"""
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.platform.authorization.models import Role, RoleScope
from src.platform.authorization.permissions import role_has_permission
from src.platform.database.session import get_db
from src.platform.identity import auth0
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import Company, Membership, MembershipStatus
from src.platform.users.models import User, UserStatus

router = APIRouter(prefix="/api/team", tags=["team"])

_MANAGE = "company.member.manage"


def _empresas_administradas(db: Session, user: User) -> set[int]:
    """IDs de empresas donde el usuario tiene membresía ACTIVA con permiso de gestión."""
    memberships = db.scalars(
        select(Membership).where(
            Membership.user_id == user.id,
            Membership.status == MembershipStatus.activo,
        )
    ).all()
    return {m.company_id for m in memberships if role_has_permission(db, m.role_id, _MANAGE)}


def _rol_de_empresa(db: Session, role_key: str) -> Role:
    role = db.scalar(select(Role).where(Role.key == role_key))
    if role is None or role.scope != RoleScope.company:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Rol inválido")
    return role


class TeamCompany(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ruc: str
    razon_social: str


class TeamMembershipItem(BaseModel):
    membership_id: int
    company_id: int
    razon_social: str
    role_key: str
    status: MembershipStatus


class TeamMember(BaseModel):
    user_id: int
    email: str
    nombre: str
    memberships: list[TeamMembershipItem]


class TeamAssignInput(BaseModel):
    email: EmailStr
    nombre: str = Field(min_length=1, max_length=150)
    role_key: str
    company_ids: list[int] = Field(min_length=1)


class TeamAssignResult(BaseModel):
    user_id: int
    email: str
    asignadas: list[int]
    ya_existentes: list[int]


class MembershipStatusInput(BaseModel):
    status: MembershipStatus


@router.get("/companies", response_model=list[TeamCompany])
def list_admin_companies(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[TeamCompany]:
    ids = _empresas_administradas(db, user)
    if not ids:
        return []
    companies = db.scalars(
        select(Company).where(Company.id.in_(ids)).order_by(Company.razon_social)
    ).all()
    return [TeamCompany.model_validate(c) for c in companies]


@router.get("/members", response_model=list[TeamMember])
def list_team_members(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[TeamMember]:
    ids = _empresas_administradas(db, user)
    if not ids:
        return []
    memberships = db.scalars(select(Membership).where(Membership.company_id.in_(ids))).all()
    if not memberships:
        return []

    # Precarga en lote (evita el N+1 al armar la respuesta agrupada por persona).
    companies = {
        c.id: c for c in db.scalars(select(Company).where(Company.id.in_(ids))).all()
    }
    users = {
        u.id: u
        for u in db.scalars(
            select(User).where(User.id.in_({m.user_id for m in memberships}))
        ).all()
    }
    roles = {
        r.id: r
        for r in db.scalars(
            select(Role).where(Role.id.in_({m.role_id for m in memberships}))
        ).all()
    }

    por_usuario: dict[int, TeamMember] = {}
    for m in memberships:
        u = users[m.user_id]
        miembro = por_usuario.get(u.id)
        if miembro is None:
            miembro = TeamMember(user_id=u.id, email=u.email, nombre=u.nombre, memberships=[])
            por_usuario[u.id] = miembro
        miembro.memberships.append(
            TeamMembershipItem(
                membership_id=m.id,
                company_id=m.company_id,
                razon_social=companies[m.company_id].razon_social,
                role_key=roles[m.role_id].key,
                status=m.status,
            )
        )
    return sorted(por_usuario.values(), key=lambda t: t.nombre.lower())


@router.post("/assign", response_model=TeamAssignResult, status_code=status.HTTP_201_CREATED)
def assign_to_companies(
    payload: TeamAssignInput,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TeamAssignResult:
    admin_ids = _empresas_administradas(db, user)
    solicitadas = list(dict.fromkeys(payload.company_ids))  # dedup, conserva el orden
    no_admin = [cid for cid in solicitadas if cid not in admin_ids]
    if no_admin:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="No administras una o más de las empresas seleccionadas",
        )
    role = _rol_de_empresa(db, payload.role_key)

    # Una sola cuenta Auth0 por persona: si el email ya existe se reutiliza; si no,
    # se crea en Auth0 y se le envía el email para establecer su contraseña.
    target = db.scalar(select(User).where(User.email == payload.email))
    if target is None:
        try:
            auth0_sub = auth0.crear_usuario(
                payload.email, payload.nombre, secrets.token_urlsafe(12) + "A1!"
            )
            auth0.enviar_reset_password(payload.email)
        except auth0.Auth0Error as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc))
        target = User(
            email=payload.email,
            nombre=payload.nombre,
            auth0_sub=auth0_sub,
            status=UserStatus.activo,
        )
        db.add(target)
        db.flush()

    asignadas: list[int] = []
    ya_existentes: list[int] = []
    for cid in solicitadas:
        existe = db.scalar(
            select(Membership).where(
                Membership.user_id == target.id, Membership.company_id == cid
            )
        )
        if existe is not None:
            ya_existentes.append(cid)
            continue
        db.add(Membership(user_id=target.id, company_id=cid, role_id=role.id))
        asignadas.append(cid)
    db.commit()
    return TeamAssignResult(
        user_id=target.id,
        email=target.email,
        asignadas=asignadas,
        ya_existentes=ya_existentes,
    )


@router.patch("/memberships/{membership_id}", response_model=TeamMembershipItem)
def set_membership_status(
    membership_id: int,
    payload: MembershipStatusInput,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> TeamMembershipItem:
    admin_ids = _empresas_administradas(db, user)
    membership = db.get(Membership, membership_id)
    if membership is None or membership.company_id not in admin_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Membresía no encontrada")
    if membership.user_id == user.id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="No puedes cambiar tu propio acceso"
        )
    membership.status = payload.status
    db.commit()
    db.refresh(membership)
    company = db.get(Company, membership.company_id)
    role = db.get(Role, membership.role_id)
    return TeamMembershipItem(
        membership_id=membership.id,
        company_id=membership.company_id,
        razon_social=company.razon_social,
        role_key=role.key,
        status=membership.status,
    )
