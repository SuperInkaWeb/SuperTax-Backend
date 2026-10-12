"""datafilter: datasets

Revision ID: e1f2a3b4c5d6
Revises: d1e2f3a4b5c6
Create Date: 2026-10-12 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS datafilter")
    op.create_table(
        'datasets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('original_filename', sa.String(length=255), nullable=False),
        sa.Column('extension', sa.String(length=20), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('origin', sa.String(length=20), nullable=False),
        sa.Column('size_bytes', sa.BigInteger(), nullable=False),
        sa.Column('row_count', sa.BigInteger(), nullable=True),
        sa.Column('columns', sa.JSON(), nullable=False),
        sa.Column('sheets', sa.JSON(), nullable=False),
        sa.Column('active_sheet', sa.String(length=100), nullable=True),
        sa.Column('parquet_path', sa.String(length=500), nullable=True),
        sa.Column('source_path', sa.String(length=500), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['core.companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['core.users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        schema='datafilter',
    )
    op.create_index(
        op.f('ix_datafilter_datasets_company_id'), 'datasets', ['company_id'], schema='datafilter'
    )
    op.create_index(
        op.f('ix_datafilter_datasets_status'), 'datasets', ['status'], schema='datafilter'
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_datafilter_datasets_status'), table_name='datasets', schema='datafilter')
    op.drop_index(op.f('ix_datafilter_datasets_company_id'), table_name='datasets', schema='datafilter')
    op.drop_table('datasets', schema='datafilter')
    op.execute("DROP SCHEMA IF EXISTS datafilter")
