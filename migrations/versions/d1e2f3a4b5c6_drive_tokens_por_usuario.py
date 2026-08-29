"""sunat: drive_tokens por usuario (antes por empresa)

Cambia la clave de `drive_tokens` de `company_id` a `user_id`: cada usuario conecta
su propio Google Drive y sus descargas suben ahí. Las conexiones existentes (por
empresa) no se pueden reasignar a un usuario, así que se descartan; cada usuario
reconecta su Drive una vez.

Revision ID: d1e2f3a4b5c6
Revises: c7d8e9f0a1b2
Create Date: 2026-08-28 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = 'd1e2f3a4b5c6'
down_revision: Union[str, None] = 'c7d8e9f0a1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Se recrea la tabla (los tokens no son reasignables a un usuario y son
    # re-obtenibles reconectando).
    op.drop_table('drive_tokens', schema='sunat')
    op.create_table(
        'drive_tokens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id',
            sa.Integer(),
            sa.ForeignKey('core.users.id', ondelete='CASCADE'),
            nullable=False,
            unique=True,
        ),
        sa.Column('access_token_enc', sa.Text(), nullable=False, server_default=''),
        sa.Column('refresh_token_enc', sa.Text(), nullable=False, server_default=''),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema='sunat',
    )


def downgrade() -> None:
    op.drop_table('drive_tokens', schema='sunat')
    op.create_table(
        'drive_tokens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'company_id',
            sa.Integer(),
            sa.ForeignKey('core.companies.id', ondelete='CASCADE'),
            nullable=False,
            unique=True,
        ),
        sa.Column('access_token_enc', sa.Text(), nullable=False, server_default=''),
        sa.Column('refresh_token_enc', sa.Text(), nullable=False, server_default=''),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        schema='sunat',
    )
