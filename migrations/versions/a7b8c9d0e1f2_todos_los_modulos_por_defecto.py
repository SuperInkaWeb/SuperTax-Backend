"""core: activa todos los módulos para toda empresa existente (provisional)

Mientras no haya suscripciones, toda empresa debe tener todos los módulos. Esta
migración rellena las empresas ya creadas: inserta como 'activo' cada par
(empresa, módulo del catálogo) que aún no exista. Las altas nuevas lo hacen en
código (activar_todos_los_modulos). Idempotente: no duplica los que ya estén.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-10-29 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO core.company_modules (company_id, module_key, status) "
        "SELECT c.id, m.key, 'activo' "
        "FROM core.companies c CROSS JOIN core.modules m "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM core.company_modules cm "
        "  WHERE cm.company_id = c.id AND cm.module_key = m.key"
        ")"
    )


def downgrade() -> None:
    # Backfill de datos provisional: no se puede distinguir qué filas agregó esta
    # migración, así que la bajada no elimina nada (no-op seguro).
    pass
