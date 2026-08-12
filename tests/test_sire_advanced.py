"""
Tests de features avanzadas SIRE (Fase 2b-3): análisis y formato de archivo
guardado, creación con mapeo/guardado de formato, y reanudar conciliaciones.
"""
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.modules.sire.domain.entities import JobStatus, TipoLibro
from src.modules.sire.infrastructure.models import ReconciliationJobModel
from src.modules.sire.infrastructure.repositories import SqlFileMappingRepository
from src.platform.authorization.models import (
    CompanyModule,
    CompanyModuleStatus,
    Role,
)
from src.platform.database.session import get_db
from src.platform.identity.current_user import get_current_user
from src.platform.storage import get_storage
from src.platform.storage.local import LocalStorage
from src.platform.tenancy.models import Company, Membership
from src.platform.users.models import User
from src.seed import seed

_ARCHIVO_COMPRAS = b"01|F001|123|100.00|18.00|118.00\n01|F001|124|200.00|36.00|236.00\n"


def _rol(db, key: str) -> Role:
    return db.scalar(select(Role).where(Role.key == key))


def _escenario(db, role_key: str = "admin_empresa"):
    seed(db)
    empresa = Company(ruc="20100000055", razon_social="Estudio Avanzado")
    user = User(email="a@estudio.pe", nombre="A", auth0_sub="auth0|a")
    db.add_all([empresa, user])
    db.flush()
    db.add(Membership(user_id=user.id, company_id=empresa.id, role_id=_rol(db, role_key).id))
    db.add(
        CompanyModule(
            company_id=empresa.id, module_key="sire", status=CompanyModuleStatus.activo
        )
    )
    db.flush()
    return user, empresa


def _override(db, user, storage=None):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    if storage is not None:
        app.dependency_overrides[get_storage] = lambda: storage


def test_analizar_archivo_devuelve_config(db_session):
    user, empresa = _escenario(db_session, role_key="operador")
    _override(db_session, user)
    try:
        resp = TestClient(app).post(
            "/api/sire/file-mapping/analizar",
            headers={"X-Company-Id": str(empresa.id)},
            data={"tipo_libro": "compras"},
            files={"archivo": ("compras.txt", _ARCHIVO_COMPRAS)},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "config" in body and "validacion" in body


def test_formato_guardado_get_y_delete(db_session):
    user, empresa = _escenario(db_session, role_key="admin_empresa")
    # Guardamos un formato directo por el repo (sin pasar la validación aritmética).
    SqlFileMappingRepository(db_session).save(
        empresa.id, "compras", {"delimiter": "|", "columnas": {"serie": 1, "numero": 2}}
    )
    _override(db_session, user)
    try:
        client = TestClient(app)
        get_resp = client.get(
            "/api/sire/file-mapping?tipo_libro=compras",
            headers={"X-Company-Id": str(empresa.id)},
        )
        assert get_resp.status_code == 200
        assert get_resp.json()["tipo_libro"] == "compras"

        del_resp = client.delete(
            "/api/sire/file-mapping?tipo_libro=compras",
            headers={"X-Company-Id": str(empresa.id)},
        )
        assert del_resp.status_code == 204
    finally:
        app.dependency_overrides.clear()

    assert SqlFileMappingRepository(db_session).get(empresa.id, "compras") is None


def test_formato_guardar_requiere_permiso_manage(db_session):
    # 'operador' no tiene sire.mapping.manage → DELETE debe dar 403.
    user, empresa = _escenario(db_session, role_key="operador")
    _override(db_session, user)
    try:
        resp = TestClient(app).delete(
            "/api/sire/file-mapping?tipo_libro=compras",
            headers={"X-Company-Id": str(empresa.id)},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_crear_job_con_mapeo_y_guardar_formato(db_session, tmp_path):
    user, empresa = _escenario(db_session, role_key="admin_empresa")
    _override(db_session, user, LocalStorage(str(tmp_path)))
    mapeo = {"delimiter": "|", "columnas": {"serie": 1, "numero": 2, "tipo_cdp": 0}}
    try:
        resp = TestClient(app).post(
            "/api/sire/jobs",
            headers={"X-Company-Id": str(empresa.id)},
            data={
                "periodo": "202601",
                "tipo_libro": "compras",
                "mapeo_columnas": json.dumps(mapeo),
                "guardar_formato": "true",
            },
            files={"archivo": ("compras.txt", _ARCHIVO_COMPRAS)},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201, resp.text
    job_id = resp.json()["id"]
    # El mapeo quedó en el job y el formato se guardó para la empresa.
    fila = db_session.get(ReconciliationJobModel, job_id)
    assert fila.mapeo_config == mapeo
    assert SqlFileMappingRepository(db_session).get(empresa.id, "compras") is not None


def test_reanudar_job_en_error(db_session, tmp_path):
    user, empresa = _escenario(db_session, role_key="operador")
    # Job en error con archivo conservado → reanudable.
    fila = ReconciliationJobModel(
        company_id=empresa.id,
        created_by_id=user.id,
        periodo="202601",
        tipo_libro=TipoLibro.compras,
        status=JobStatus.error,
        empresa_file_path="sire/uploads/x/1/e.csv",
        error_message="fallo previo",
    )
    db_session.add(fila)
    db_session.flush()

    _override(db_session, user, LocalStorage(str(tmp_path)))
    try:
        client = TestClient(app)
        ok = client.post(
            f"/api/sire/jobs/{fila.id}/resume",
            headers={"X-Company-Id": str(empresa.id)},
        )
        assert ok.status_code == 200
        assert ok.json()["status"] == "en_cola"

        # Reanudar uno inexistente → 404.
        nf = client.post(
            "/api/sire/jobs/99999/resume", headers={"X-Company-Id": str(empresa.id)}
        )
        assert nf.status_code == 404
    finally:
        app.dependency_overrides.clear()
