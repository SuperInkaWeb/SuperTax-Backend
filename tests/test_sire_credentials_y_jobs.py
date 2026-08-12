"""
Tests de la Fase 2b-1: cifrado, storage, credenciales SUNAT y creación de jobs.
Cubren el pipeline de seguridad completo y que los secretos nunca se exponen.
"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.modules.sire.infrastructure.models import SireCredentialsModel
from src.platform.authorization.models import (
    CompanyModule,
    CompanyModuleStatus,
    Role,
)
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.security import decrypt_field, encrypt_field
from src.platform.storage import get_storage
from src.platform.storage.local import LocalStorage
from src.platform.tenancy.models import Company, Membership
from src.platform.users.models import User
from src.seed import seed


# ───────────────────── Unidad: cifrado y storage ─────────────────────
def test_cifrado_roundtrip():
    original = "MiClaveSOL-123"
    cifrado = encrypt_field(original)
    assert cifrado != original
    assert decrypt_field(cifrado) == original


def test_storage_roundtrip(tmp_path):
    storage = LocalStorage(str(tmp_path))
    ruta = storage.save("a/b/c.txt", b"contenido")
    assert storage.exists(ruta)
    assert storage.read(ruta) == b"contenido"
    assert storage.size(ruta) == len(b"contenido")
    storage.delete(ruta)
    assert not storage.exists(ruta)


# ───────────────────── Integración: endpoints ─────────────────────
def _rol(db, key: str) -> Role:
    return db.scalar(select(Role).where(Role.key == key))


def _escenario(db, role_key: str = "operador"):
    seed(db)
    empresa = Company(ruc="20100000009", razon_social="Estudio Z")
    user = User(email="z@estudio.pe", nombre="Z", auth0_sub="auth0|z")
    db.add_all([empresa, user])
    db.flush()
    db.add(Membership(user_id=user.id, company_id=empresa.id, role_id=_rol(db, role_key).id))
    db.add(
        CompanyModule(
            company_id=empresa.id,
            module_key="sire",
            status=CompanyModuleStatus.activo,
        )
    )
    db.flush()
    return user, empresa


def _override(db, user, storage=None):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    if storage is not None:
        app.dependency_overrides[get_storage] = lambda: storage


def test_credenciales_se_cifran_y_no_se_exponen(db_session):
    user, empresa = _escenario(db_session, role_key="admin_empresa")
    _override(db_session, user)
    try:
        resp = TestClient(app).put(
            "/api/sire/credentials",
            headers={"X-Company-Id": str(empresa.id)},
            json={
                "usuario_sol": "USUARIO1",
                "clave_sol": "secretoSOL",
                "client_id": "cid-123",
                "client_secret": "secretCID",
            },
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is True
    assert body["usuario_sol"] == "USUARIO1"
    assert "clave_sol" not in body and "client_secret" not in body  # secretos nunca

    creds = db_session.scalar(
        select(SireCredentialsModel).where(SireCredentialsModel.company_id == empresa.id)
    )
    assert creds.clave_sol_enc != "secretoSOL"  # cifrado en reposo
    assert decrypt_field(creds.clave_sol_enc) == "secretoSOL"


def test_crear_job_guarda_archivo_y_queda_en_cola(db_session, tmp_path):
    user, empresa = _escenario(db_session, role_key="operador")
    storage = LocalStorage(str(tmp_path))
    _override(db_session, user, storage)
    try:
        resp = TestClient(app).post(
            "/api/sire/jobs",
            headers={"X-Company-Id": str(empresa.id)},
            data={"periodo": "202601", "tipo_libro": "compras"},
            files={"archivo": ("empresa.csv", b"col1|col2\n1|2\n")},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "en_cola"
    assert body["empresa_filename"] == "empresa.csv"
    assert storage.exists(f"sire/uploads/{empresa.id}/{body['id']}/empresa.csv")


def test_crear_job_periodo_invalido_da_400(db_session, tmp_path):
    user, empresa = _escenario(db_session, role_key="operador")
    _override(db_session, user, LocalStorage(str(tmp_path)))
    try:
        resp = TestClient(app).post(
            "/api/sire/jobs",
            headers={"X-Company-Id": str(empresa.id)},
            data={"periodo": "2026", "tipo_libro": "compras"},
            files={"archivo": ("e.csv", b"x")},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 400


def test_crear_job_sin_permiso_da_403(db_session, tmp_path):
    user, empresa = _escenario(db_session, role_key="consulta")  # solo lectura
    _override(db_session, user, LocalStorage(str(tmp_path)))
    try:
        resp = TestClient(app).post(
            "/api/sire/jobs",
            headers={"X-Company-Id": str(empresa.id)},
            data={"periodo": "202601", "tipo_libro": "compras"},
            files={"archivo": ("e.csv", b"x")},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_descargar_reporte_inexistente_da_404(db_session, tmp_path):
    user, empresa = _escenario(db_session, role_key="operador")
    _override(db_session, user, LocalStorage(str(tmp_path)))
    try:
        resp = TestClient(app).get(
            "/api/sire/jobs/999/report", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404
