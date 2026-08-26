"""scanner: tipo_forzado en scanner_jobs (selector de tipo manual)

Revision ID: c7d8e9f0a1b2
Revises: f1a2b3c4d5e6
Create Date: 2026-08-26 10:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'scanner_jobs',
        sa.Column('tipo_forzado', sa.String(length=50), nullable=True),
        schema='scanner',
    )


def downgrade() -> None:
    op.drop_column('scanner_jobs', 'tipo_forzado', schema='scanner')
