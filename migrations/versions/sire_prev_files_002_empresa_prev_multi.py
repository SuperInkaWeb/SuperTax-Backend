"""sire: varios PLE de meses previos en la conciliación (req 3 duplicidad histórica)

Columna opcional `empresa_prev_file_paths` (JSON: lista de rutas) en
reconciliation_jobs: el usuario puede subir VARIOS PLE de meses previos para la
duplicidad contra todos ellos; la correlatividad usa el de periodo más reciente.
Reemplaza a `empresa_prev_file_path` (que se conserva para leer jobs antiguos).

Revision ID: sire_prev_files_002
Revises: sire_prev_file_001
Create Date: 2026-11-11 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "sire_prev_files_002"
down_revision: Union[str, None] = "sire_prev_file_001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reconciliation_jobs",
        sa.Column("empresa_prev_file_paths", sa.JSON(), nullable=True),
        schema="sire",
    )


def downgrade() -> None:
    op.drop_column("reconciliation_jobs", "empresa_prev_file_paths", schema="sire")
