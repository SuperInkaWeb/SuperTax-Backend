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
from src.platform.tenancy.models import Company, Estudio, EstudioMember, Membership
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

    # La aprobación crea un estudio para el nuevo contador, con la empresa colgada
    # de él y el usuario como admin del estudio (tapa el hueco de estudio_id).
    estudio = db_session.scalar(select(Estudio).where(Estudio.owner_user_id == user.id))
    assert estudio is not None
    assert company.estudio_id == estudio.id
    miembro = db_session.scalar(
        select(EstudioMember).where(
            EstudioMember.estudio_id == estudio.id, EstudioMember.user_id == user.id
        )
    )
    assert miembro is not None
    assert db_session.get(Role, miembro.role_id).key == "admin_empresa"


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


def test_superadmin_crea_empresa_asignada_a_un_estudio(db_session):
    seed(db_session)
    admin = _platform_admin(db_session)
    owner = User(email="dueno@e.pe", nombre="Dueño", auth0_sub="auth0|dueno")
    db_session.add(owner)
    db_session.flush()
    estudio = Estudio(nombre="Estudio Dueño", owner_user_id=owner.id)
    db_session.add(estudio)
    db_session.flush()

    _override(db_session, admin)
    try:
        resp = TestClient(app).post(
            "/api/companies",
            json={"ruc": "20666666666", "razon_social": "Asignada SAC", "estudio_id": estudio.id},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    company = db_session.scalar(select(Company).where(Company.ruc == "20666666666"))
    assert company.estudio_id == estudio.id


def test_traspaso_mueve_empresa_a_otro_estudio(db_session):
    seed(db_session)
    superadmin = _platform_admin(db_session)
    admin_role = db_session.scalar(select(Role).where(Role.key == "admin_empresa"))

    def _estudio(email, ruc=None):
        owner = User(email=email, nombre=email, auth0_sub=f"auth0|{email}")
        db_session.add(owner)
        db_session.flush()
        est = Estudio(nombre=f"Estudio {email}", owner_user_id=owner.id)
        db_session.add(est)
        db_session.flush()
        db_session.add(
            EstudioMember(estudio_id=est.id, user_id=owner.id, role_id=admin_role.id)
        )
        comp = None
        if ruc:
            comp = Company(ruc=ruc, razon_social="Cliente", estudio_id=est.id)
            db_session.add(comp)
            db_session.flush()
            # El dueño del origen tiene además membresía directa (el creador).
            db_session.add(
                Membership(user_id=owner.id, company_id=comp.id, role_id=admin_role.id)
            )
        db_session.flush()
        return owner, est, comp

    owner_a, _est_a, company = _estudio("a@e.pe", ruc="20444444444")
    owner_b, est_b, _ = _estudio("b@e.pe")

    _override(db_session, superadmin)
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/companies/{company.id}/transfer", json={"estudio_id": est_b.id}
        )
        assert resp.status_code == 200, resp.text

        # El admin del estudio destino ahora la ve; el del origen ya no.
        app.dependency_overrides[get_current_user] = lambda: owner_b
        me_b = client.get("/me").json()
        app.dependency_overrides[get_current_user] = lambda: owner_a
        me_a = client.get("/me").json()
    finally:
        app.dependency_overrides.clear()

    db_session.refresh(company)
    assert company.estudio_id == est_b.id
    assert "20444444444" in {c["ruc"] for c in me_b["companies"]}
    assert "20444444444" not in {c["ruc"] for c in me_a["companies"]}


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
