"""
Entorno de Alembic.

Toma la URL de la base de datos desde la configuración central (variables de
entorno) y el `metadata` de la Base declarativa. Conforme se creen modelos
(Fase 1+), impórtalos aquí para que `--autogenerate` los detecte.
"""
from alembic import context
from sqlalchemy import engine_from_config, pool

# Modelos del núcleo Core — su import registra las tablas en Base.metadata
# para que `--autogenerate` las detecte.
from src.platform.authorization import models as _authz_models  # noqa: E402, F401
from src.platform.config.settings import settings
from src.platform.database.base import Base
from src.platform.tenancy import models as _tenancy_models  # noqa: E402, F401
from src.platform.users import models as _users_models  # noqa: E402, F401

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        {"sqlalchemy.url": settings.DATABASE_URL},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
