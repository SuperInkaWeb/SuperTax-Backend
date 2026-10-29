"""
Tests del núcleo Core: RBAC (permisos por rol), entitlements de módulos y
resolución de membresía (Modelo B). Usan Postgres real (aislados por rollback).
"""
from sqlalchemy import select

from src.platform.authorization.entitlements import company_has_module
from src.platform.authorization.models import (
    CompanyModule,
    CompanyModuleStatus,
    Permission,
    Role,
)
from src.platform.authorization.permissions import role_has_permission
from src.platform.tenancy.models import (
    Company,
    Estudio,
    EstudioMember,
    Membership,
)
from src.platform.tenancy.service import estudio_admin_role_id, get_active_membership
from src.platform.users.models import User
from src.seed import seed


def _crear_empresa(db) -> Company:
    company = Company(ruc="20123456789", razon_social="Estudio Contable X")
    db.add(company)
    db.flush()
    return company


def test_role_has_permission(db_session):
    perm = Permission(key="sire.job.create")
    role = Role(key="operador", nombre="Operador")
    role.permissions.append(perm)
    db_session.add(role)
    db_session.flush()

    assert role_has_permission(db_session, role.id, "sire.job.create") is True
    assert role_has_permission(db_session, role.id, "sire.job.approve") is False


def test_company_has_module(db_session):
    company = _crear_empresa(db_session)
    db_session.add(
        CompanyModule(
            company_id=company.id,
            module_key="scanner",
            status=CompanyModuleStatus.activo,
        )
    )
    db_session.flush()

    assert company_has_module(db_session, company.id, "scanner") is True
    # Módulo no contratado → la doble puerta se cierra
    assert company_has_module(db_session, company.id, "sire") is False


def test_company_module_suspendido_no_da_acceso(db_session):
    company = _crear_empresa(db_session)
    db_session.add(
        CompanyModule(
            company_id=company.id,
            module_key="sunat",
            status=CompanyModuleStatus.suspendido,
        )
    )
    db_session.flush()

    assert company_has_module(db_session, company.id, "sunat") is False


def test_get_active_membership(db_session):
    company = _crear_empresa(db_session)
    role = Role(key="admin_empresa", nombre="Admin Empresa")
    user = User(email="contador@estudio.pe", nombre="Contador", auth0_sub="auth0|abc")
    db_session.add_all([role, user])
    db_session.flush()
    db_session.add(
        Membership(user_id=user.id, company_id=company.id, role_id=role.id)
    )
    db_session.flush()

    membership = get_active_membership(db_session, user.id, company.id)
    assert membership is not None
    assert membership.role_id == role.id

    # Un usuario que no pertenece a la empresa no obtiene membresía
    assert get_active_membership(db_session, user_id=999, company_id=company.id) is None


def _estudio_con_miembro(db, role_key: str) -> tuple[Estudio, User]:
    role = Role(key=role_key, nombre=role_key)
    user = User(email=f"{role_key}@e.pe", nombre=role_key, auth0_sub=f"auth0|{role_key}")
    db.add_all([role, user])
    db.flush()
    estudio = Estudio(nombre="Estudio", owner_user_id=user.id)
    db.add(estudio)
    db.flush()
    db.add(EstudioMember(estudio_id=estudio.id, user_id=user.id, role_id=role.id))
    db.flush()
    return estudio, user


def test_estudio_admin_role_id_concede_al_admin(db_session):
    estudio, user = _estudio_con_miembro(db_session, "admin_empresa")
    role_id = estudio_admin_role_id(db_session, user.id, estudio.id)
    assert role_id is not None
    # Sin estudio o usuario ajeno → sin acceso total
    assert estudio_admin_role_id(db_session, user.id, None) is None
    assert estudio_admin_role_id(db_session, user_id=999, estudio_id=estudio.id) is None


def test_estudio_miembro_no_admin_no_da_acceso_total(db_session):
    # Un operador de estudio NO ve toda la cartera (eso se asigna por cliente).
    estudio, user = _estudio_con_miembro(db_session, "operador")
    assert estudio_admin_role_id(db_session, user.id, estudio.id) is None


def test_seed_define_los_cuatro_roles(db_session):
    seed(db_session)

    def permisos(key: str) -> set[str]:
        role = db_session.scalar(select(Role).where(Role.key == key))
        return {p.key for p in role.permissions}

    operador = permisos("operador")
    asistente = permisos("asistente")
    consulta = permisos("consulta")

    # Operador (Contador): opera todo el módulo, incluido borrar y credenciales.
    assert {"datafilter.dataset.delete", "sire.credentials.manage", "sire.job.approve"} <= operador
    # Asistente: día a día SIN borrar ni gestionar credenciales.
    assert {"sire.job.create", "sire.job.approve"} <= asistente
    assert "datafilter.dataset.delete" not in asistente
    assert "sire.credentials.manage" not in asistente
    # Consulta: solo lectura.
    assert "sire.job.read" in consulta
    assert all(key.endswith(".read") for key in consulta)
