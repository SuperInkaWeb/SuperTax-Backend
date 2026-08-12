import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from src.main import app

# Registrar los modelos (Core + módulos) en Base.metadata
from src.modules.sire.infrastructure import models as _sire  # noqa: F401
from src.platform.authorization import models as _authz  # noqa: F401
from src.platform.config.settings import settings
from src.platform.database.base import Base
from src.platform.tenancy import models as _tenancy  # noqa: F401
from src.platform.users import models as _users  # noqa: F401


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


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
    session = sessionmaker(bind=connection)()
    # Cada test parte de tablas vacías (aislado del seed de desarrollo).
    # El TRUNCATE es transaccional: el rollback final restaura el estado previo.
    session.execute(
        text(
            "TRUNCATE core.role_permissions, core.memberships, core.company_modules, "
            "core.users, core.companies, core.roles, core.permissions, core.modules, "
            "sire.reconciliation_results, sire.reconciliation_jobs "
            "RESTART IDENTITY CASCADE"
        )
    )
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()
