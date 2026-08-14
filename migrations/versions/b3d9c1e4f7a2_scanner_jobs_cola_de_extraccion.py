"""scanner: cola de extraccion (scanner_jobs)

Revision ID: b3d9c1e4f7a2
Revises: 3270c83ef3e4
Create Date: 2026-08-13 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'b3d9c1e4f7a2'
down_revision: Union[str, None] = '3270c83ef3e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'scanner_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('nombre_archivo', sa.String(length=255), nullable=False),
        sa.Column('storage_path', sa.String(length=500), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'en_cola', 'procesando', 'completado', 'error',
                name='scanner_job_status',
            ),
            nullable=False,
        ),
        sa.Column('documento_id', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['core.companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['core.users.id']),
        sa.ForeignKeyConstraint(
            ['documento_id'], ['scanner.documentos.id'], ondelete='SET NULL'
        ),
        sa.PrimaryKeyConstraint('id'),
        schema='scanner',
    )
    op.create_index(
        op.f('ix_scanner_scanner_jobs_company_id'),
        'scanner_jobs', ['company_id'], unique=False, schema='scanner',
    )
    op.create_index(
        op.f('ix_scanner_scanner_jobs_status'),
        'scanner_jobs', ['status'], unique=False, schema='scanner',
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_scanner_scanner_jobs_status'),
        table_name='scanner_jobs', schema='scanner',
    )
    op.drop_index(
        op.f('ix_scanner_scanner_jobs_company_id'),
        table_name='scanner_jobs', schema='scanner',
    )
    op.drop_table('scanner_jobs', schema='scanner')
    sa.Enum(name='scanner_job_status').drop(op.get_bind())
