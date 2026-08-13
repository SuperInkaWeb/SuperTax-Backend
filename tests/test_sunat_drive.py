"""
Tests de la conexión Google Drive (Fase 3c). El intercambio real con Google no
es testeable; se verifica la lógica: firma/lectura del state, auth URL,
desconexión y callback con state inválido.
"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.modules.sunat.application import drive_service
from src.modules.sunat.infrastructure.models import DriveTokenModel
from src.platform.authorization.models import (
    CompanyModule,
    CompanyModuleStatus,
    Role,
)
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.tenancy.models import Company, Membership
from src.platform.users.models import User
from src.seed import seed


def _rol(db, key: str) -> Role:
    return db.scalar(select(Role).where(Role.key == key))


def _escenario(db, role_key: str = "admin_empresa"):
    seed(db)
    empresa = Company(ruc="20700000002", razon_social="Estudio Drive")
    user = User(email="d@estudio.pe", nombre="D", auth0_sub="auth0|d")
    db.add_all([empresa, user])
    db.flush()
    db.add(Membership(user_id=user.id, company_id=empresa.id, role_id=_rol(db, role_key).id))
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


def test_state_firma_y_lectura():
    state = drive_service._firmar_state(company_id=7, user_id=3)
    assert drive_service._leer_state(state) == 7


def test_state_invalido_lanza_error():
    try:
        drive_service._leer_state("basura")
        raise AssertionError("debió lanzar DriveError")
    except drive_service.DriveError:
        pass


def test_auth_url_contiene_google(db_session):
    user, empresa = _escenario(db_session, role_key="admin_empresa")
    _override(db_session, user)
    try:
        resp = TestClient(app).get(
            "/api/sunat/drive/auth", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert "accounts.google.com" in resp.json()["url"]


def test_callback_state_invalido_devuelve_html_error(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        resp = TestClient(app).get("/api/sunat/drive/callback?code=x&state=basura")
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 400
    assert "Estado inválido" in resp.text


def test_desconectar_elimina_token(db_session):
    user, empresa = _escenario(db_session, role_key="admin_empresa")
    db_session.add(
        DriveTokenModel(company_id=empresa.id, access_token_enc="x", refresh_token_enc="y")
    )
    db_session.flush()
    _override(db_session, user)
    try:
        resp = TestClient(app).post(
            "/api/sunat/drive/desconectar", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert (
        db_session.scalar(
            select(DriveTokenModel).where(DriveTokenModel.company_id == empresa.id)
        )
        is None
    )
