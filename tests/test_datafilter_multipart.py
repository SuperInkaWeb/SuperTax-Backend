"""
Subida directa a R2 (multipart): ciclo de vida del dataset en el repositorio.

Verifica la lógica de estado que soporta la subida directa (uploading → pending),
que es la parte nueva comprobable sin R2. Las llamadas a R2 (firmar/completar) y
los endpoints HTTP se validan en staging, no en tests (requieren el bucket real).
"""
import pytest

from src.modules.datafilter.infrastructure.models import DatasetStatus
from src.modules.datafilter.infrastructure.repositories import SqlDatasetRepository
from src.platform.tenancy.models import Company
from src.platform.users.models import User


@pytest.fixture
def empresa_y_usuario(db_session):
    company = Company(ruc="20481516273", razon_social="Estudio DataFilter")
    user = User(email="df@estudio.pe", nombre="DF", auth0_sub="auth0|df")
    db_session.add_all([company, user])
    db_session.commit()
    return company, user


def test_ciclo_subida_directa_uploading_a_pending(db_session, empresa_y_usuario):
    company, user = empresa_y_usuario
    repo = SqlDatasetRepository(db_session)

    ds = repo.create_uploading(
        company_id=company.id,
        created_by_id=user.id,
        original_filename="ventas.csv",
        extension=".csv",
    )
    assert ds.status == DatasetStatus.uploading.value
    assert ds.source_path is None and ds.multipart_upload_id is None

    repo.set_multipart(ds.id, source_path="datafilter/1/1/source.csv", upload_id="upl-123")
    guardado = repo.get(ds.id, company.id)
    assert guardado.source_path == "datafilter/1/1/source.csv"
    assert guardado.multipart_upload_id == "upl-123"

    listo = repo.complete_upload(ds.id, company.id, size_bytes=4_600_000_000)
    assert listo is not None
    assert listo.status == DatasetStatus.pending.value  # listo para ingesta
    assert listo.size_bytes == 4_600_000_000
    assert listo.multipart_upload_id is None  # la subida terminó


def test_complete_upload_es_idempotente(db_session, empresa_y_usuario):
    """Completar dos veces (o sobre un dataset que ya no está subiéndose) devuelve
    None: evita re-encolar la ingesta de la misma subida."""
    company, user = empresa_y_usuario
    repo = SqlDatasetRepository(db_session)
    ds = repo.create_uploading(
        company_id=company.id, created_by_id=user.id, original_filename="x.csv", extension=".csv"
    )
    repo.set_multipart(ds.id, "datafilter/1/1/source.csv", "upl-1")

    assert repo.complete_upload(ds.id, company.id, 100) is not None
    assert repo.complete_upload(ds.id, company.id, 100) is None  # ya no está en uploading


def test_complete_upload_aisla_por_empresa(db_session, empresa_y_usuario):
    """Una empresa no puede cerrar la subida de otra (aislamiento multi-tenant)."""
    company, user = empresa_y_usuario
    repo = SqlDatasetRepository(db_session)
    ds = repo.create_uploading(
        company_id=company.id, created_by_id=user.id, original_filename="x.csv", extension=".csv"
    )
    repo.set_multipart(ds.id, "datafilter/1/1/source.csv", "upl-1")

    assert repo.complete_upload(ds.id, company_id=company.id + 999, size_bytes=100) is None
