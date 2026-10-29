"""core: membresía de estudio (equipo del estudio con rol a nivel estudio)

Fase 2 del modelo de estudios. `core.estudio_members` modela el equipo del
estudio (varios admins posibles). El rol admin a nivel estudio da acceso total a
todas las empresas del estudio.

Backfill: el owner de cada estudio pasa a ser miembro con rol admin_empresa. No
cambia la visibilidad hoy (el owner ya era admin de sus empresas), pero habilita
que un admin vea las empresas que agregue otro admin del mismo estudio.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-10-28 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "estudio_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "estudio_id",
            sa.Integer(),
            sa.ForeignKey("core.estudios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("core.users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role_id", sa.Integer(), sa.ForeignKey("core.roles.id"), nullable=False
        ),
        sa.Column(
            "status", sa.Enum("activo", "inactivo", name="estudio_member_status"),
            nullable=False, server_default="activo",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("estudio_id", "user_id", name="uq_estudio_member"),
        schema="core",
    )
    op.create_index(
        "ix_core_estudio_members_estudio_id", "estudio_members", ["estudio_id"],
        schema="core",
    )
    op.create_index(
        "ix_core_estudio_members_user_id", "estudio_members", ["user_id"],
        schema="core",
    )
    op.create_index(
        "ix_core_estudio_members_role_id", "estudio_members", ["role_id"],
        schema="core",
    )

    _backfill(op.get_bind())


def _backfill(conn) -> None:
    """Agrega al owner de cada estudio como miembro admin."""
    admin_role_id = conn.execute(
        sa.text("SELECT id FROM core.roles WHERE key = 'admin_empresa'")
    ).scalar()
    if admin_role_id is None:
        return  # Sin rol admin (seed no corrido): nada que hacer.

    conn.execute(
        sa.text(
            "INSERT INTO core.estudio_members "
            "(estudio_id, user_id, role_id, status, created_at) "
            "SELECT e.id, e.owner_user_id, :admin, 'activo', now() "
            "FROM core.estudios e "
            "ON CONFLICT (estudio_id, user_id) DO NOTHING"
        ),
        {"admin": admin_role_id},
    )


def downgrade() -> None:
    op.drop_index("ix_core_estudio_members_role_id", "estudio_members", schema="core")
    op.drop_index("ix_core_estudio_members_user_id", "estudio_members", schema="core")
    op.drop_index(
        "ix_core_estudio_members_estudio_id", "estudio_members", schema="core"
    )
    op.drop_table("estudio_members", schema="core")
    sa.Enum(name="estudio_member_status").drop(op.get_bind(), checkfirst=True)
