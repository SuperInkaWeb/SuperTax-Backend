"""
Tests del módulo Scanner (Fase 4a): listado y edición de documentos a través del
pipeline de autorización + aislamiento por empresa.
"""
from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.modules.scanner.infrastructure.models import DocumentoModel
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


def _escenario(db, role_key: str = "operador", con_modulo: bool = True):
    seed(db)
    empresa = Company(ruc="20800000001", razon_social="Estudio Scan")
    user = User(email="sc@estudio.pe", nombre="Sc", auth0_sub="auth0|sc")
    db.add_all([empresa, user])
    db.flush()
    db.add(Membership(user_id=user.id, company_id=empresa.id, role_id=_rol(db, role_key).id))
    if con_modulo:
        db.add(
            CompanyModule(
                company_id=empresa.id, module_key="scanner", status=CompanyModuleStatus.activo
            )
        )
    db.flush()
    return user, empresa


def _doc(db, company_id: int, user_id: int, **extra) -> DocumentoModel:
    doc = DocumentoModel(
        company_id=company_id,
        created_by_id=user_id,
        tipo_documento="recibo_luz",
        tipo_etiqueta="Recibo de luz",
        confianza=0.9,
        nombre_archivo="recibo.pdf",
        campos={"total": "100.00"},
        **extra,
    )
    db.add(doc)
    db.flush()
    return doc


def _override(db, user):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db


def test_listar_documentos_vacio(db_session):
    user, empresa = _escenario(db_session, role_key="operador")
    _override(db_session, user)
    try:
        resp = TestClient(app).get(
            "/api/scanner/documentos", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json() == []


def test_modulo_scanner_no_contratado_da_403(db_session):
    user, empresa = _escenario(db_session, role_key="operador", con_modulo=False)
    _override(db_session, user)
    try:
        resp = TestClient(app).get(
            "/api/scanner/documentos", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_actualizar_campos_hace_merge(db_session):
    user, empresa = _escenario(db_session, role_key="operador")
    doc = _doc(db_session, empresa.id, user.id)
    _override(db_session, user)
    try:
        resp = TestClient(app).put(
            f"/api/scanner/documentos/{doc.id}",
            headers={"X-Company-Id": str(empresa.id)},
            json={"campos": {"ruc": "20123456789"}},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    campos = resp.json()["campos"]
    assert campos["ruc"] == "20123456789"
    assert campos["total"] == "100.00"  # conservó el existente


def test_actualizar_documento_de_otra_empresa_da_404(db_session):
    user, empresa = _escenario(db_session, role_key="operador")
    otra = Company(ruc="20800000099", razon_social="Otra")
    db_session.add(otra)
    db_session.flush()
    doc_ajeno = _doc(db_session, otra.id, user.id)
    _override(db_session, user)
    try:
        resp = TestClient(app).put(
            f"/api/scanner/documentos/{doc_ajeno.id}",
            headers={"X-Company-Id": str(empresa.id)},
            json={"campos": {"x": "1"}},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 404


def test_tipos_documento(db_session):
    user, empresa = _escenario(db_session, role_key="consulta")  # solo read
    _override(db_session, user)
    try:
        resp = TestClient(app).get(
            "/api/scanner/tipos-documento", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert "factura_electronica" in body
    assert "etiqueta" in body["factura_electronica"]


def test_actualizar_sin_permiso_da_403(db_session):
    # 'consulta' tiene scanner.doc.read pero NO scanner.doc.update.
    user, empresa = _escenario(db_session, role_key="consulta")
    doc = _doc(db_session, empresa.id, user.id)
    _override(db_session, user)
    try:
        resp = TestClient(app).put(
            f"/api/scanner/documentos/{doc.id}",
            headers={"X-Company-Id": str(empresa.id)},
            json={"campos": {"x": "1"}},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 403


def test_subir_encola_job(db_session):
    user, empresa = _escenario(db_session, role_key="operador")
    _override(db_session, user)
    try:
        resp = TestClient(app).post(
            "/api/scanner/upload/auto",
            headers={"X-Company-Id": str(empresa.id)},
            files={"file": ("recibo.pdf", b"%PDF-1.4 contenido", "application/pdf")},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "en_cola"
    assert isinstance(body["job_id"], int)


def test_subir_formato_invalido_da_400(db_session):
    user, empresa = _escenario(db_session, role_key="operador")
    _override(db_session, user)
    try:
        resp = TestClient(app).post(
            "/api/scanner/upload/auto",
            headers={"X-Company-Id": str(empresa.id)},
            files={"file": ("nota.txt", b"hola", "text/plain")},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 400


def test_estado_job_encolado(db_session):
    from src.modules.scanner.infrastructure.models import ScannerJobModel

    user, empresa = _escenario(db_session, role_key="operador")
    job = ScannerJobModel(
        company_id=empresa.id,
        created_by_id=user.id,
        nombre_archivo="x.pdf",
        storage_path="scanner/uploads/x",
    )
    db_session.add(job)
    db_session.flush()
    _override(db_session, user)
    try:
        resp = TestClient(app).get(
            f"/api/scanner/jobs/{job.id}", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "en_cola"
    assert body["documento"] is None
