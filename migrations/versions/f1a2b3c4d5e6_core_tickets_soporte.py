"""core tickets soporte

Revision ID: f1a2b3c4d5e6
Revises: c8e2f5a71d93
Create Date: 2026-08-14 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, None] = 'c8e2f5a71d93'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'tickets',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('company_id', sa.Integer(), nullable=False),
        sa.Column('created_by_id', sa.Integer(), nullable=False),
        sa.Column('asunto', sa.String(length=200), nullable=False),
        sa.Column(
            'status',
            sa.Enum('abierto', 'respondido', 'cerrado', name='ticket_status'),
            nullable=False,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['company_id'], ['core.companies.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['created_by_id'], ['core.users.id']),
        sa.PrimaryKeyConstraint('id'),
        schema='core',
    )
    op.create_index(
        op.f('ix_core_tickets_company_id'), 'tickets', ['company_id'], unique=False, schema='core'
    )
    op.create_index(
        op.f('ix_core_tickets_status'), 'tickets', ['status'], unique=False, schema='core'
    )
    op.create_table(
        'ticket_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('ticket_id', sa.Integer(), nullable=False),
        sa.Column('author_id', sa.Integer(), nullable=False),
        sa.Column('es_soporte', sa.Boolean(), nullable=False),
        sa.Column('mensaje', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['ticket_id'], ['core.tickets.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['author_id'], ['core.users.id']),
        sa.PrimaryKeyConstraint('id'),
        schema='core',
    )
    op.create_index(
        op.f('ix_core_ticket_messages_ticket_id'),
        'ticket_messages',
        ['ticket_id'],
        unique=False,
        schema='core',
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_core_ticket_messages_ticket_id'), table_name='ticket_messages', schema='core'
    )
    op.drop_table('ticket_messages', schema='core')
    op.drop_index(op.f('ix_core_tickets_status'), table_name='tickets', schema='core')
    op.drop_index(op.f('ix_core_tickets_company_id'), table_name='tickets', schema='core')
    op.drop_table('tickets', schema='core')
    op.execute("DROP TYPE IF EXISTS ticket_status")
