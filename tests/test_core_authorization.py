"""
Tests del núcleo Core: RBAC (permisos por rol), entitlements de módulos y
resolución de membresía (Modelo B). Usan Postgres real (aislados por rollback).
"""
from src.platform.authorization.entitlements import company_has_module
from src.platform.authorization.models import (
    CompanyModule,
    CompanyModuleStatus,
    Permission,
    Role,
)
from src.platform.authorization.permissions import role_has_permission
from src.platform.tenancy.models import Company, Membership
from src.platform.tenancy.service import get_active_membership
from src.platform.users.models import User


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
