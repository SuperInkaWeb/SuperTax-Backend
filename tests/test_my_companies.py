"""Tests del alta de empresas self-service: un usuario crea su empresa y queda como Admin."""
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import Company, Membership
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
    membresia = db_session.scalar(
        select(Membership).where(
            Membership.user_id == user.id, Membership.company_id == company.id
        )
    )
    assert membresia is not None


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
