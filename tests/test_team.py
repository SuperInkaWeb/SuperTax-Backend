"""
Tests de gestión de equipo (Fase 3): el admin de estudio administra toda su
cartera y asigna clientes concretos a no-admins (acceso acotado). Auth0 mockeado.
"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.platform.authorization.models import Role
from src.platform.database.session import get_db
from src.platform.identity import auth0
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import Company, Estudio, EstudioMember
from src.platform.users.models import User
from src.seed import seed


def _mock_auth0(monkeypatch):
    monkeypatch.setattr(auth0, "crear_usuario", lambda email, nombre, pwd: f"auth0|{email}")
    monkeypatch.setattr(auth0, "enviar_reset_password", lambda email: None)


def _override(db, user):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user


def _estudio_admin(db) -> tuple[User, Estudio, Company, Company]:
    """Un admin de estudio con 2 clientes y SIN membresía directa en ninguno."""
    seed(db)
    admin_role = db.scalar(select(Role).where(Role.key == "admin_empresa"))
    owner = User(email="owner@estudio.pe", nombre="Owner", auth0_sub="auth0|owner")
    db.add(owner)
    db.flush()
    estudio = Estudio(nombre="Estudio", owner_user_id=owner.id)
    db.add(estudio)
    db.flush()
    c1 = Company(ruc="20111111111", razon_social="Cliente 1", estudio_id=estudio.id)
    c2 = Company(ruc="20222222222", razon_social="Cliente 2", estudio_id=estudio.id)
    db.add_all([c1, c2])
    db.flush()
    db.add(EstudioMember(estudio_id=estudio.id, user_id=owner.id, role_id=admin_role.id))
    db.flush()
    return owner, estudio, c1, c2


def test_team_companies_incluye_toda_la_cartera_del_estudio(db_session):
    owner, _estudio, _c1, _c2 = _estudio_admin(db_session)
    _override(db_session, owner)
    try:
        resp = TestClient(app).get("/api/team/companies")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    # Las ve aunque no tenga membresía directa: las administra por ser admin del estudio.
    assert {c["ruc"] for c in resp.json()} == {"20111111111", "20222222222"}


def test_assign_da_acceso_solo_a_los_clientes_elegidos(db_session, monkeypatch):
    _mock_auth0(monkeypatch)
    owner, _estudio, c1, _c2 = _estudio_admin(db_session)
    _override(db_session, owner)
    try:
        client = TestClient(app)
        alta = client.post(
            "/api/team/assign",
            json={
                "email": "op@estudio.pe",
                "nombre": "Operador",
                "role_key": "operador",
                "company_ids": [c1.id],
            },
        )
        assert alta.status_code == 201

        # El no-admin recién asignado ve SOLO el cliente que se le dio.
        nuevo = db_session.scalar(select(User).where(User.email == "op@estudio.pe"))
        app.dependency_overrides[get_current_user] = lambda: nuevo
        me = client.get("/me")
    finally:
        app.dependency_overrides.clear()

    assert me.status_code == 200
    companies = me.json()["companies"]
    assert {c["ruc"] for c in companies} == {"20111111111"}
    assert companies[0]["role_key"] == "operador"


def test_assign_rechaza_empresa_fuera_del_estudio(db_session, monkeypatch):
    _mock_auth0(monkeypatch)
    owner, _estudio, _c1, _c2 = _estudio_admin(db_session)
    ajena = Company(ruc="20999999999", razon_social="De otro estudio")
    db_session.add(ajena)
    db_session.flush()
    _override(db_session, owner)
    try:
        resp = TestClient(app).post(
            "/api/team/assign",
            json={
                "email": "x@e.pe",
                "nombre": "X",
                "role_key": "operador",
                "company_ids": [ajena.id],
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
