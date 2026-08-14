import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.main import app

# Registrar los modelos (Core + módulos) en Base.metadata
from src.modules.scanner.infrastructure import models as _scanner  # noqa: F401
from src.modules.sire.infrastructure import models as _sire  # noqa: F401
from src.modules.sunat.infrastructure import models as _sunat  # noqa: F401
from src.platform.authorization import models as _authz  # noqa: F401
from src.platform.config.settings import settings
from src.platform.database.base import Base
from src.platform.onboarding import models as _onboarding  # noqa: F401
from src.platform.tenancy import models as _tenancy  # noqa: F401
from src.platform.users import models as _users  # noqa: F401


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    """Los limitadores viven en memoria del proceso: se limpian entre tests."""
    from src.modules.sire.api.routes import _limite_conciliacion
    from src.platform.onboarding.api import _limite_solicitudes

    _limite_conciliacion.clear_all()
    _limite_solicitudes.clear_all()
    yield


@pytest.fixture
def db_session():
    """
    Sesión sobre Postgres real, aislada por transacción (rollback al terminar).
    Si no hay Postgres disponible, el test se omite (los de salud siguen pasando).
    """
    try:
        engine = create_engine(settings.DATABASE_URL)
        connection = engine.connect()
    except Exception:
        pytest.skip("Postgres no disponible")

    with engine.begin() as setup:
        setup.execute(text("CREATE SCHEMA IF NOT EXISTS core"))
        setup.execute(text("CREATE SCHEMA IF NOT EXISTS sire"))
    Base.metadata.create_all(engine, checkfirst=True)

    transaction = connection.begin()
    # Cada test parte de tablas vacías (aislado del seed de desarrollo).
    # El TRUNCATE va en la transacción externa; el rollback final lo revierte.
    connection.execute(
        text(
            "TRUNCATE core.role_permissions, core.memberships, core.company_modules, "
            "core.access_requests, core.users, core.companies, core.roles, "
            "core.permissions, core.modules, "
            "sire.company_credentials, sire.company_file_mappings, "
            "sire.report_files, sire.reconciliation_results, "
            "sire.reconciliation_jobs, "
            "sunat.sunat_credentials, sunat.drive_tokens, sunat.job_results, "
            "scanner.documentos RESTART IDENTITY CASCADE"
        )
    )
    # join_transaction_mode="create_savepoint": los commits de los repositorios
    # ocurren sobre un savepoint, así el rollback externo los revierte igual.
    session = sessionmaker(
        bind=connection, join_transaction_mode="create_savepoint"
    )()
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
