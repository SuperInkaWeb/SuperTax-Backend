"""
Tests de integración del módulo SIRE a través de todo el pipeline de seguridad:
identidad → empresa activa → entitlement de módulo → permiso → aislamiento.
"""
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select

from src.main import app
from src.modules.sire.domain.entities import JobStatus, TipoLibro
from src.modules.sire.infrastructure.models import ReconciliationJobModel
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


def _role(db, key: str) -> Role:
    return db.scalar(select(Role).where(Role.key == key))


def _job(db, company_id: int, user_id: int, periodo: str) -> ReconciliationJobModel:
    job = ReconciliationJobModel(
        company_id=company_id,
        created_by_id=user_id,
        periodo=periodo,
        tipo_libro=TipoLibro.compras,
        status=JobStatus.completado,
        created_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.flush()
    return job


def _escenario(db, role_key: str, con_modulo: bool = True):
    """Crea usuario + 2 empresas (A activa) con un job cada una. Devuelve (user, empresa_a)."""
    seed(db)  # roles base + permisos del módulo sire
    empresa_a = Company(ruc="20100000001", razon_social="Estudio A")
    empresa_b = Company(ruc="20100000002", razon_social="Otra Empresa B")
    user = User(email="op@estudio.pe", nombre="Op", auth0_sub="auth0|op")
    db.add_all([empresa_a, empresa_b, user])
    db.flush()

    db.add(Membership(user_id=user.id, company_id=empresa_a.id, role_id=_role(db, role_key).id))
    if con_modulo:
        db.add(
            CompanyModule(
                company_id=empresa_a.id,
                module_key="sire",
                status=CompanyModuleStatus.activo,
            )
        )
    _job(db, empresa_a.id, user.id, "202601")
    _job(db, empresa_b.id, user.id, "202602")  # de otra empresa: NO debe verse
    db.flush()
    return user, empresa_a


def _get(db, user, company_id: int, path: str):
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = lambda: db
    try:
        return TestClient(app).get(path, headers={"X-Company-Id": str(company_id)})
    finally:
        app.dependency_overrides.clear()


def test_lista_jobs_solo_de_la_empresa_activa(db_session):
    user, empresa_a = _escenario(db_session, role_key="operador")
    resp = _get(db_session, user, empresa_a.id, "/api/sire/jobs")

    assert resp.status_code == 200
    jobs = resp.json()
    assert len(jobs) == 1  # solo el de la empresa A, NO el de la B
    assert jobs[0]["periodo"] == "202601"


def test_modulo_no_contratado_devuelve_403(db_session):
    user, empresa_a = _escenario(db_session, role_key="operador", con_modulo=False)
    resp = _get(db_session, user, empresa_a.id, "/api/sire/jobs")
    assert resp.status_code == 403
    assert "no está contratado" in resp.json()["message"]


def test_rol_sin_permiso_devuelve_403(db_session):
    # Rol nuevo sin permisos SIRE.
    db_session.add(Role(key="sin_permisos", nombre="Sin permisos"))
    db_session.flush()
    user, empresa_a = _escenario(db_session, role_key="sin_permisos")
    resp = _get(db_session, user, empresa_a.id, "/api/sire/jobs")
    assert resp.status_code == 403
    assert "permiso" in resp.json()["message"].lower()


def test_job_de_otra_empresa_no_es_accesible(db_session):
    user, empresa_a = _escenario(db_session, role_key="operador")
    # El job de la empresa B existe, pero con la empresa A activa no se alcanza.
    job_b = db_session.scalar(
        select(ReconciliationJobModel).where(ReconciliationJobModel.periodo == "202602")
    )
    resp = _get(db_session, user, empresa_a.id, f"/api/sire/jobs/{job_b.id}")
    assert resp.status_code == 404
