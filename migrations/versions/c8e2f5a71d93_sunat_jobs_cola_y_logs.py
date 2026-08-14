"""sunat: cola de descargas (sunat_jobs) y logs (sunat_job_logs)

Revision ID: c8e2f5a71d93
Revises: b3d9c1e4f7a2
Create Date: 2026-08-13 13:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'c8e2f5a71d93'
down_revision: Union[str, None] = 'b3d9c1e4f7a2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'sunat_jobs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.String(length=64), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column(
            'status',
            sa.Enum(
                'en_cola', 'procesando', 'completado', 'error', 'cancelado',
                name='sunat_job_status',
            ),
            nullable=False,
        ),
        sa.Column('config_enc', sa.Text(), nullable=False),
        sa.Column('excel_path', sa.String(length=500), nullable=False),
        sa.Column('cancel_requested', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['company_id'], ['core.companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['core.users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('job_id'),
        schema='sunat',
    )
    op.create_index(
        op.f('ix_sunat_sunat_jobs_job_id'),
        'sunat_jobs', ['job_id'], unique=True, schema='sunat',
    )
    op.create_index(
        op.f('ix_sunat_sunat_jobs_company_id'),
        'sunat_jobs', ['company_id'], unique=False, schema='sunat',
    )
    op.create_index(
        op.f('ix_sunat_sunat_jobs_status'),
        'sunat_jobs', ['status'], unique=False, schema='sunat',
    )
    op.create_table(
        'sunat_job_logs',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('job_id', sa.String(length=64), nullable=False),
        sa.Column('kind', sa.String(length=10), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        schema='sunat',
    )
    op.create_index(
        op.f('ix_sunat_sunat_job_logs_job_id'),
        'sunat_job_logs', ['job_id'], unique=False, schema='sunat',
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_sunat_sunat_job_logs_job_id'),
        table_name='sunat_job_logs', schema='sunat',
    )
    op.drop_table('sunat_job_logs', schema='sunat')
    op.drop_index(op.f('ix_sunat_sunat_jobs_status'), table_name='sunat_jobs', schema='sunat')
    op.drop_index(op.f('ix_sunat_sunat_jobs_company_id'), table_name='sunat_jobs', schema='sunat')
    op.drop_index(op.f('ix_sunat_sunat_jobs_job_id'), table_name='sunat_jobs', schema='sunat')
    op.drop_table('sunat_jobs', schema='sunat')
    sa.Enum(name='sunat_job_status').drop(op.get_bind())
