"""sire: archivo del mes anterior en la conciliación (reqs 3/4)

Columna opcional `empresa_prev_file_path` en reconciliation_jobs: ruta del PLE del
mes anterior que el usuario sube para la duplicidad y correlatividad entre
periodos. Nullable (no todos los jobs lo usan).

Revision ID: sire_prev_file_001
Revises: d0e1f2a3b4c5
Create Date: 2026-11-10 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "sire_prev_file_001"
down_revision: Union[str, None] = "d0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_jobs",
        sa.Column("empresa_prev_file_path", sa.String(500), nullable=True),
        schema="sire",
    )


def downgrade() -> None:
    op.drop_column("reconciliation_jobs", "empresa_prev_file_path", schema="sire")
