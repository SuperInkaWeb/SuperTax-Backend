"""
Tests del módulo SUNAT (Fase 3a): credenciales SOL cifradas, entitlement,
historial y estado de Drive, a través del pipeline de autorización.
"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.modules.sunat.infrastructure.models import SunatCredentialsModel
from src.platform.authorization.models import (
    CompanyModule,
    CompanyModuleStatus,
    Role,
)
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.security import decrypt_field
from src.platform.tenancy.models import Company, Membership
from src.platform.users.models import User
from src.seed import seed


def _rol(db, key: str) -> Role:
    return db.scalar(select(Role).where(Role.key == key))


def _escenario(db, role_key: str = "admin_empresa", con_modulo: bool = True):
    seed(db)
    empresa = Company(ruc="20700000001", razon_social="Estudio SUNAT")
    user = User(email="s@estudio.pe", nombre="S", auth0_sub="auth0|s")
    db.add_all([empresa, user])
    db.flush()
    db.add(Membership(user_id=user.id, company_id=empresa.id, role_id=_rol(db, role_key).id))
    if con_modulo:
        db.add(
            CompanyModule(
                company_id=empresa.id, module_key="sunat", status=CompanyModuleStatus.activo
            )
        )
    db.flush()
    return user, empresa


def _override(db, user):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db


def test_credenciales_sunat_se_cifran(db_session):
    user, empresa = _escenario(db_session, role_key="admin_empresa")
    _override(db_session, user)
    try:
        resp = TestClient(app).put(
            "/api/sunat/credentials",
            headers={"X-Company-Id": str(empresa.id)},
            json={"ruc": "20700000001", "usuario": "USUARIO1", "clave": "claveSOL"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True and body["ruc"] == "20700000001"
    assert "clave" not in body and "usuario" not in body  # secretos nunca

    creds = db_session.scalar(
        select(SunatCredentialsModel).where(SunatCredentialsModel.company_id == empresa.id)
    )
    assert creds.clave_enc != "claveSOL"
    assert decrypt_field(creds.clave_enc) == "claveSOL"
    assert decrypt_field(creds.usuario_enc) == "USUARIO1"


def test_modulo_sunat_no_contratado_da_403(db_session):
    user, empresa = _escenario(db_session, role_key="admin_empresa", con_modulo=False)
    _override(db_session, user)
    try:
        resp = TestClient(app).get(
            "/api/sunat/jobs", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_listar_jobs_vacio(db_session):
    user, empresa = _escenario(db_session, role_key="operador")
    _override(db_session, user)
    try:
        resp = TestClient(app).get(
            "/api/sunat/jobs", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == []


def test_credenciales_requiere_permiso_manage(db_session):
    # 'operador' no tiene sunat.credentials.manage → 403.
    user, empresa = _escenario(db_session, role_key="operador")
    _override(db_session, user)
    try:
        resp = TestClient(app).get(
            "/api/sunat/credentials", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_drive_no_conectado(db_session):
    user, empresa = _escenario(db_session, role_key="admin_empresa")
    _override(db_session, user)
    try:
        resp = TestClient(app).get(
            "/api/sunat/drive", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == {"connected": False}


# ─────────────────────── Jobs (3b) ───────────────────────
def test_email_valido():
    from src.modules.sunat.application.job_service import _es_email_valido

    assert _es_email_valido("a@b.pe") is True
    assert _es_email_valido("no-es-email") is False
    assert _es_email_valido("") is False


def test_iniciar_con_correo_invalido_da_400(db_session):
    user, empresa = _escenario(db_session, role_key="operador")
    _override(db_session, user)
    try:
        resp = TestClient(app).post(
            "/api/sunat/iniciar",
            headers={"X-Company-Id": str(empresa.id)},
            data={
                "ruc": "20700000001",
                "usuario": "U",
                "clave": "C",
                "usar_correo": "true",
                "destino": "no-es-email",
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 400


def test_logs_job_inexistente_da_404(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        resp = TestClient(app).get("/api/sunat/logs/inexistente?token=x")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404
