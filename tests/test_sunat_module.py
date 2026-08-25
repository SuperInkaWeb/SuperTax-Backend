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
    # El usuario SOL se expone (para prellenar el form); la clave NUNCA.
    assert "clave" not in body
    assert body["usuario"] == "USUARIO1"

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


def test_detalle_job_devuelve_resultados(db_session):
    import json

    from src.modules.sunat.infrastructure.models import JobResultModel

    user, empresa = _escenario(db_session, role_key="operador")
    resultados = [{"id": "F001-1", "estado": "Parcial", "pdf": True, "xml": False}]
    db_session.add(
        JobResultModel(
            job_id="job-xyz",
            company_id=empresa.id,
            created_by_id=user.id,
            resultados=json.dumps(resultados),
        )
    )
    db_session.flush()
    _override(db_session, user)
    try:
        resp = TestClient(app).get(
            "/api/sunat/jobs/job-xyz", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert resp.json()["resultados"][0]["estado"] == "Parcial"


def test_iniciar_encola_job_cifrando_config(db_session):
    from src.modules.sunat.infrastructure.models import SunatJobModel, SunatJobStatus

    user, empresa = _escenario(db_session, role_key="operador")
    _override(db_session, user)
    try:
        resp = TestClient(app).post(
            "/api/sunat/iniciar",
            headers={"X-Company-Id": str(empresa.id)},
            data={"ruc": "20700000001", "usuario": "U", "clave": "claveSOL"},
            files={"excel": ("libro.xlsx", b"PK-fake-xlsx", "application/octet-stream")},
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    assert "job_id" in resp.json()

    job = db_session.scalar(
        select(SunatJobModel).where(SunatJobModel.company_id == empresa.id)
    )
    assert job is not None
    assert job.status == SunatJobStatus.en_cola
    assert "claveSOL" not in job.config_enc  # la config va cifrada


def test_cancelar_marca_cancel_requested(db_session):
    from src.modules.sunat.infrastructure.models import SunatJobModel

    user, empresa = _escenario(db_session, role_key="operador")
    job = SunatJobModel(
        job_id="job-cancel",
        company_id=empresa.id,
        created_by_id=user.id,
        config_enc="x",
        excel_path="sunat/uploads/x.xlsx",
    )
    db_session.add(job)
    db_session.flush()
    _override(db_session, user)
    try:
        resp = TestClient(app).post(
            "/api/sunat/cancelar/job-cancel", headers={"X-Company-Id": str(empresa.id)}
        )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    db_session.refresh(job)
    assert job.cancel_requested is True


# ── input_parser: entrada flexible ───────────────────────────────────────────
def _xlsx(filas: list[list]) -> bytes:
    import io

    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    for fila in filas:
        ws.append(fila)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_input_parser_excel_real_no_revienta_utf8():
    """Regresión: extraer_comprobantes debe normalizar el Excel binario antes de
    leerlo (si no, read_csv lo decodifica como UTF-8 y truena)."""
    import io as _io

    import pandas as pd

    from src.modules.sunat.infrastructure import input_parser as ip
    from src.modules.sunat.infrastructure.automation.selectores import COLUMNAS_REQUERIDAS

    content = _xlsx([
        ["REPORTE COMPRAS - COMPAÑÍA ÑUÑOA S.A.C.", None, None, None],
        ["Serie", "Numero", "RUC Emisor", "Tipo Comprobante"],
        ["F001", 173, "20100070970", "Factura"],
        ["B002", 45, "20512345678", "Boleta de Venta"],
    ])
    mapeo = ip.analizar(content)["mapeo"]
    assert mapeo.is_usable
    comps = ip.extraer_comprobantes(content, mapeo)  # antes: UnicodeDecodeError
    assert [c.id for c in comps] == ["F001-173", "B002-45"]
    assert comps[0].tipo_num == 1 and comps[1].tipo_num == 3  # texto → código

    xlsx = ip.a_excel_canonico(comps)
    df = pd.read_excel(_io.BytesIO(xlsx))
    assert list(df.columns) == COLUMNAS_REQUERIDAS  # lo que espera automatizar()
