"""
Tests del Core de administración: onboarding (solicitudes), empresas,
entitlements de módulos y miembros. Auth0 (Management API) va mockeado.
"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.platform.authorization.models import Role
from src.platform.database.session import get_db
from src.platform.identity import auth0
from src.platform.identity.current_user import get_current_user
from src.platform.onboarding.models import AccessRequest
from src.platform.tenancy.models import Company, Membership
from src.platform.users.models import User
from src.seed import seed


def _mock_auth0(monkeypatch):
    monkeypatch.setattr(auth0, "crear_usuario", lambda email, nombre, pwd: f"auth0|{email}")
    monkeypatch.setattr(auth0, "enviar_reset_password", lambda email: None)


def _platform_admin(db) -> User:
    admin = User(
        email="super@plat.pe", nombre="Super", auth0_sub="auth0|super", is_platform_admin=True
    )
    db.add(admin)
    db.flush()
    return admin


def _override(db, user=None):
    app.dependency_overrides[get_db] = lambda: db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user


# ─────────────────────── Onboarding ───────────────────────
def test_solicitud_de_acceso_publica_y_duplicada(db_session):
    _override(db_session)
    try:
        client = TestClient(app)
        payload = {
            "email": "nuevo@e.pe",
            "nombre": "Nuevo",
            "empresa_nombre": "Estudio",
            "ruc": "20111111111",
        }
        assert client.post("/api/access-requests", json=payload).status_code == 201
        assert client.post("/api/access-requests", json=payload).status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_listar_solicitudes_requiere_platform_admin(db_session):
    seed(db_session)
    normal = User(email="u@e.pe", nombre="U", auth0_sub="auth0|u")
    db_session.add(normal)
    db_session.flush()
    _override(db_session, normal)
    try:
        assert TestClient(app).get("/api/access-requests").status_code == 403
    finally:
        app.dependency_overrides.clear()


def test_aprobar_solicitud_crea_empresa_usuario_y_membresia(db_session, monkeypatch):
    seed(db_session)
    _mock_auth0(monkeypatch)
    admin = _platform_admin(db_session)
    req = AccessRequest(
        email="cli@e.pe", nombre="Cli", empresa_nombre="Estudio Cli", ruc="20222222222"
    )
    db_session.add(req)
    db_session.flush()
    _override(db_session, admin)
    try:
        resp = TestClient(app).put(
            f"/api/access-requests/{req.id}/review", json={"status": "aprobado"}
        )
        assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.clear()

    company = db_session.scalar(select(Company).where(Company.ruc == "20222222222"))
    user = db_session.scalar(select(User).where(User.email == "cli@e.pe"))
    assert company is not None
    assert user is not None and user.auth0_sub == "auth0|cli@e.pe"
    membership = db_session.scalar(select(Membership).where(Membership.user_id == user.id))
    assert membership is not None
    assert db_session.get(Role, membership.role_id).key == "admin_empresa"


# ─────────────────────── Empresas y módulos ───────────────────────
def test_crear_empresa_y_habilitar_modulo(db_session):
    seed(db_session)  # crea el módulo 'sire'
    admin = _platform_admin(db_session)
    _override(db_session, admin)
    try:
        client = TestClient(app)
        creada = client.post(
            "/api/companies", json={"ruc": "20333333333", "razon_social": "Nueva SAC"}
        )
        assert creada.status_code == 201
        company_id = creada.json()["id"]

        activar = client.post(
            f"/api/companies/{company_id}/modules", json={"module_key": "sire"}
        )
        assert activar.status_code == 201
        assert client.delete(f"/api/companies/{company_id}/modules/sire").status_code == 204
    finally:
        app.dependency_overrides.clear()


# ─────────────────────── Miembros ───────────────────────
def test_invitar_miembro_a_la_empresa(db_session, monkeypatch):
    seed(db_session)
    _mock_auth0(monkeypatch)
    company = Company(ruc="20555555555", razon_social="Estudio M")
    admin = User(email="adm@e.pe", nombre="Adm", auth0_sub="auth0|adm")
    db_session.add_all([company, admin])
    db_session.flush()
    admin_role = db_session.scalar(select(Role).where(Role.key == "admin_empresa"))
    db_session.add(
        Membership(user_id=admin.id, company_id=company.id, role_id=admin_role.id)
    )
    db_session.flush()
    _override(db_session, admin)
    try:
        resp = TestClient(app).post(
            "/api/members",
            headers={"X-Company-Id": str(company.id)},
            json={"email": "nuevo@e.pe", "nombre": "Nuevo", "role_key": "operador"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["role_key"] == "operador"
    finally:
        app.dependency_overrides.clear()

    invitado = db_session.scalar(select(User).where(User.email == "nuevo@e.pe"))
    assert invitado is not None and invitado.auth0_sub == "auth0|nuevo@e.pe"


def test_invitar_sin_permiso_da_403(db_session):
    seed(db_session)
    company = Company(ruc="20666666666", razon_social="Estudio C")
    user = User(email="cons@e.pe", nombre="Cons", auth0_sub="auth0|cons")
    db_session.add_all([company, user])
    db_session.flush()
    consulta = db_session.scalar(select(Role).where(Role.key == "consulta"))  # sin manage
    db_session.add(Membership(user_id=user.id, company_id=company.id, role_id=consulta.id))
    db_session.flush()
    _override(db_session, user)
    try:
        resp = TestClient(app).post(
            "/api/members",
            headers={"X-Company-Id": str(company.id)},
            json={"email": "x@e.pe", "nombre": "X", "role_key": "operador"},
        )
        assert resp.status_code == 403
    finally:
        app.dependency_overrides.clear()
