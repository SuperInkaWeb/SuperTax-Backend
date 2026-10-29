"""core: solicitudes de acceso a una empresa existente (RUC ya registrado)

Fase 5. Un usuario ya registrado solicita acceso a una empresa cuyo RUC ya
pertenece a otro estudio; el admin dueño aprueba concediéndole una membresía.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-10-29 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "company_access_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "requester_user_id",
            sa.Integer(),
            sa.ForeignKey("core.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Integer(),
            sa.ForeignKey("core.companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("mensaje", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("pendiente", "aprobado", "rechazado", name="company_access_request_status"),
            nullable=False,
            server_default="pendiente",
        ),
        sa.Column("role_id", sa.Integer(), sa.ForeignKey("core.roles.id"), nullable=True),
        sa.Column(
            "reviewed_by_id", sa.Integer(), sa.ForeignKey("core.users.id"), nullable=True
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        schema="core",
    )
    op.create_index(
        "ix_core_company_access_requests_requester_user_id",
        "company_access_requests", ["requester_user_id"], schema="core",
    )
    op.create_index(
        "ix_core_company_access_requests_company_id",
        "company_access_requests", ["company_id"], schema="core",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_core_company_access_requests_company_id",
        "company_access_requests", schema="core",
    )
    op.drop_index(
        "ix_core_company_access_requests_requester_user_id",
        "company_access_requests", schema="core",
    )
    op.drop_table("company_access_requests", schema="core")
    sa.Enum(name="company_access_request_status").drop(op.get_bind(), checkfirst=True)
