"""
Tests de la Fase 5: co-admins del estudio (estudio_api) y solicitar acceso a un
RUC ya registrado (company_access_api). Auth0 mockeado.
"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.platform.authorization.models import Role
from src.platform.database.session import get_db
from src.platform.identity import auth0
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import (
    Company,
    CompanyAccessRequest,
    Estudio,
    EstudioMember,
)
from src.platform.users.models import User
from src.seed import seed


def _mock_auth0(monkeypatch):
    monkeypatch.setattr(auth0, "crear_usuario", lambda email, nombre, pwd: f"auth0|{email}")
    monkeypatch.setattr(auth0, "enviar_reset_password", lambda email: None)


def _override(db, user):
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user


def _estudio_con_admin(db, email="owner@e.pe") -> tuple[User, Estudio, Company, Company]:
    seed(db)
    admin_role = db.scalar(select(Role).where(Role.key == "admin_empresa"))
    owner = User(email=email, nombre="Owner", auth0_sub=f"auth0|{email}")
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


# ─────────────────────── Pieza 1: co-admins ───────────────────────
def test_invitar_coadmin_le_da_toda_la_cartera(db_session, monkeypatch):
    _mock_auth0(monkeypatch)
    owner, estudio, _c1, _c2 = _estudio_con_admin(db_session)
    _override(db_session, owner)
    try:
        client = TestClient(app)
        alta = client.post("/api/estudio/admins", json={"email": "co@e.pe", "nombre": "Co"})
        assert alta.status_code == 201, alta.text

        estudios = client.get("/api/estudio").json()
        assert len(estudios) == 1
        assert len(estudios[0]["members"]) == 2  # owner + co-admin

        # El co-admin ve TODA la cartera del estudio.
        co = db_session.scalar(select(User).where(User.email == "co@e.pe"))
        app.dependency_overrides[get_current_user] = lambda: co
        me = client.get("/me").json()
    finally:
        app.dependency_overrides.clear()

    assert {c["ruc"] for c in me["companies"]} == {"20111111111", "20222222222"}
    assert all(c["role_key"] == "admin_empresa" for c in me["companies"])


def test_quitar_coadmin_lo_desactiva(db_session, monkeypatch):
    _mock_auth0(monkeypatch)
    owner, estudio, _c1, _c2 = _estudio_con_admin(db_session)
    _override(db_session, owner)
    try:
        client = TestClient(app)
        client.post("/api/estudio/admins", json={"email": "co@e.pe", "nombre": "Co"})
        co = db_session.scalar(select(User).where(User.email == "co@e.pe"))
        assert client.delete(f"/api/estudio/admins/{co.id}").status_code == 204
        # No puede quitarse a sí mismo.
        assert client.delete(f"/api/estudio/admins/{owner.id}").status_code == 400
    finally:
        app.dependency_overrides.clear()

    # El co-admin desactivado ya no ve la cartera.
    co = db_session.scalar(select(User).where(User.email == "co@e.pe"))
    _override(db_session, co)
    try:
        me = TestClient(app).get("/me").json()
    finally:
        app.dependency_overrides.clear()
    assert me["companies"] == []


# ─────────────────────── Pieza 2: solicitar acceso ───────────────────────
def test_solicitar_y_aprobar_acceso_a_ruc_existente(db_session):
    owner, _estudio, c1, _c2 = _estudio_con_admin(db_session)
    externo = User(email="externo@e.pe", nombre="Externo", auth0_sub="auth0|ext")
    db_session.add(externo)
    db_session.flush()

    client = TestClient(app)
    # El externo solicita acceso al RUC de c1.
    _override(db_session, externo)
    try:
        sol = client.post("/api/company-access-requests", json={"ruc": "20111111111"})
        assert sol.status_code == 201, sol.text
    finally:
        app.dependency_overrides.clear()

    # El admin dueño ve la solicitud y la aprueba como 'operador'.
    _override(db_session, owner)
    try:
        inbox = client.get("/api/company-access-requests").json()
        assert len(inbox) == 1
        req_id = inbox[0]["id"]
        rev = client.put(
            f"/api/company-access-requests/{req_id}/review",
            json={"status": "aprobado", "role_key": "operador"},
        )
        assert rev.status_code == 200, rev.text
    finally:
        app.dependency_overrides.clear()

    # El externo ahora ve SOLO esa empresa, como operador.
    _override(db_session, externo)
    try:
        me = client.get("/me").json()
    finally:
        app.dependency_overrides.clear()
    assert {c["ruc"] for c in me["companies"]} == {"20111111111"}
    assert me["companies"][0]["role_key"] == "operador"

    solicitud = db_session.get(CompanyAccessRequest, req_id)
    assert solicitud.status.value == "aprobado"


def test_solicitar_acceso_a_ruc_inexistente_da_404(db_session):
    owner, *_ = _estudio_con_admin(db_session)
    _override(db_session, owner)
    try:
        resp = TestClient(app).post(
            "/api/company-access-requests", json={"ruc": "20999999999"}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404


def test_revisar_solicitud_solo_el_admin_dueno(db_session):
    owner, _estudio, c1, _c2 = _estudio_con_admin(db_session)
    externo = User(email="externo@e.pe", nombre="Externo", auth0_sub="auth0|ext")
    ajeno = User(email="ajeno@e.pe", nombre="Ajeno", auth0_sub="auth0|ajeno")
    db_session.add_all([externo, ajeno])
    db_session.flush()
    solicitud = CompanyAccessRequest(requester_user_id=externo.id, company_id=c1.id)
    db_session.add(solicitud)
    db_session.flush()

    # Un usuario que no administra el estudio dueño no puede revisarla.
    _override(db_session, ajeno)
    try:
        resp = TestClient(app).put(
            f"/api/company-access-requests/{solicitud.id}/review",
            json={"status": "aprobado", "role_key": "operador"},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403
