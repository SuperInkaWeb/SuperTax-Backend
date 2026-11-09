"""Tests del alta de empresas self-service: un usuario crea su empresa y queda como Admin."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.platform.authorization.models import (
    CompanyModule,
    CompanyModuleStatus,
    Module,
    Role,
)
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import Company, Estudio, EstudioMember, Membership
from src.platform.users.models import User
from src.seed import seed


def _override(db, user):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db


def _user(db) -> User:
    seed(db)
    user = User(email="c@estudio.pe", nombre="Contador", auth0_sub="auth0|c")
    db.add(user)
    db.flush()
    return user


def test_crear_mi_empresa_me_hace_admin(db_session):
    user = _user(db_session)
    _override(db_session, user)
    try:
        resp = TestClient(app).post(
            "/api/my-companies",
            json={"ruc": "20555666777", "razon_social": "Cliente Uno SAC"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 201
    assert resp.json()["ruc"] == "20555666777"

    company = db_session.scalar(select(Company).where(Company.ruc == "20555666777"))
    assert company is not None

    # La empresa queda colgada del estudio propio del usuario (del que es dueño).
    estudio = db_session.scalar(
        select(Estudio).where(Estudio.owner_user_id == user.id)
    )
    assert estudio is not None
    assert company.estudio_id == estudio.id

    # El dueño queda como admin del ESTUDIO, no con una membresía por empresa.
    miembro = db_session.scalar(
        select(EstudioMember).where(
            EstudioMember.estudio_id == estudio.id, EstudioMember.user_id == user.id
        )
    )
    assert miembro is not None
    assert db_session.get(Role, miembro.role_id).key == "admin_empresa"
    assert (
        db_session.scalar(select(Membership).where(Membership.company_id == company.id))
        is None
    )


def test_crear_dos_empresas_reusa_el_mismo_estudio(db_session):
    user = _user(db_session)
    _override(db_session, user)
    try:
        cliente = TestClient(app)
        cliente.post("/api/my-companies", json={"ruc": "20555666777", "razon_social": "Uno"})
        cliente.post("/api/my-companies", json={"ruc": "20555666888", "razon_social": "Dos"})
    finally:
        app.dependency_overrides.clear()

    estudios = db_session.scalars(
        select(Estudio).where(Estudio.owner_user_id == user.id)
    ).all()
    assert len(estudios) == 1
    companies = db_session.scalars(
        select(Company).where(Company.estudio_id == estudios[0].id)
    ).all()
    assert {c.ruc for c in companies} == {"20555666777", "20555666888"}


def test_crear_mi_empresa_activa_todos_los_modulos(db_session):
    """Provisional (sin suscripciones): el alta activa todo el catálogo."""
    user = _user(db_session)
    _override(db_session, user)
    try:
        TestClient(app).post(
            "/api/my-companies",
            json={"ruc": "20555666777", "razon_social": "Cliente SAC"},
        )
    finally:
        app.dependency_overrides.clear()

    company = db_session.scalar(select(Company).where(Company.ruc == "20555666777"))
    catalogo = {key for (key,) in db_session.execute(select(Module.key)).all()}
    activos = {
        key
        for (key,) in db_session.execute(
            select(CompanyModule.module_key).where(
                CompanyModule.company_id == company.id,
                CompanyModule.status == CompanyModuleStatus.activo,
            )
        ).all()
    }
    assert catalogo and activos == catalogo


def test_coadmin_agrega_empresa_al_estudio_compartido(db_session):
    """Un co-admin (no dueño) agrega una empresa: debe ir al estudio COMPARTIDO,
    no crearle un estudio aparte (regresión: antes se resolvía solo por owner)."""
    seed(db_session)
    admin_role = db_session.scalar(select(Role).where(Role.key == "admin_empresa"))
    dueno = User(email="dueno@e.pe", nombre="Dueño", auth0_sub="auth0|dueno")
    coadmin = User(email="co@e.pe", nombre="Co", auth0_sub="auth0|co")
    db_session.add_all([dueno, coadmin])
    db_session.flush()
    estudio = Estudio(nombre="Estudio Compartido", owner_user_id=dueno.id)
    db_session.add(estudio)
    db_session.flush()
    db_session.add_all([
        EstudioMember(estudio_id=estudio.id, user_id=dueno.id, role_id=admin_role.id),
        EstudioMember(estudio_id=estudio.id, user_id=coadmin.id, role_id=admin_role.id),
    ])
    db_session.flush()

    _override(db_session, coadmin)
    try:
        resp = TestClient(app).post(
            "/api/my-companies", json={"ruc": "20555666777", "razon_social": "Nueva"}
        )
        assert resp.status_code == 201
    finally:
        app.dependency_overrides.clear()

    company = db_session.scalar(select(Company).where(Company.ruc == "20555666777"))
    assert company.estudio_id == estudio.id  # al estudio compartido, no a uno nuevo
    # No se le creó un estudio propio al co-admin.
    assert db_session.scalar(
        select(Estudio).where(Estudio.owner_user_id == coadmin.id)
    ) is None


def test_crear_mi_empresa_ruc_duplicado_da_409(db_session):
    user = _user(db_session)
    db_session.add(Company(ruc="20555666777", razon_social="Ya existe"))
    db_session.flush()
    _override(db_session, user)
    try:
        resp = TestClient(app).post(
            "/api/my-companies",
            json={"ruc": "20555666777", "razon_social": "Otra"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 409


def test_crear_mi_empresa_ruc_corto_da_422(db_session):
    user = _user(db_session)
    _override(db_session, user)
    try:
        resp = TestClient(app).post(
            "/api/my-companies",
            json={"ruc": "123", "razon_social": "Corta"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 422
