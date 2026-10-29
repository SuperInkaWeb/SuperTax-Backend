"""core: solicitud de acceso a empresa puede ser pública (sin cuenta aún)

requester_user_id pasa a nullable y se agregan requester_email/requester_nombre
para las solicitudes públicas (la persona pide acceso a un RUC existente antes de
tener cuenta; al aprobar, el admin dueño provisiona el usuario).

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-10-29 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "company_access_requests", "requester_user_id",
        schema="core", existing_type=sa.Integer(), nullable=True,
    )
    op.add_column(
        "company_access_requests",
        sa.Column("requester_email", sa.String(length=255), nullable=True),
        schema="core",
    )
    op.add_column(
        "company_access_requests",
        sa.Column("requester_nombre", sa.String(length=150), nullable=True),
        schema="core",
    )


def downgrade() -> None:
    op.drop_column("company_access_requests", "requester_nombre", schema="core")
    op.drop_column("company_access_requests", "requester_email", schema="core")
    op.execute(
        "DELETE FROM core.company_access_requests WHERE requester_user_id IS NULL"
    )
    op.alter_column(
        "company_access_requests", "requester_user_id",
        schema="core", existing_type=sa.Integer(), nullable=False,
    )
