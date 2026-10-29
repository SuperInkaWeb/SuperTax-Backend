"""core: entidad Estudio (grupo contable) y estudio_id en companies

Fase 1 del modelo de estudios: cada empresa pasa a pertenecer a un estudio (el
inquilino dueño de la cartera). No cambia ninguna regla de visibilidad todavía.

Backfill NO destructivo: por cada empresa se resuelve su dueño (la membresía
admin más antigua; si no hay admin, la membresía más antigua), se crea un estudio
por dueño y se enlaza la empresa. Las memberships no se tocan. Las empresas sin
ninguna membresía quedan con estudio_id NULL (columna nullable a propósito) para
revisarlas aparte antes de forzar NOT NULL.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-10-28 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "estudios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("nombre", sa.String(length=200), nullable=False),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("core.users.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="core",
    )
    op.create_index(
        "ix_core_estudios_owner_user_id", "estudios", ["owner_user_id"], schema="core"
    )

    op.add_column(
        "companies",
        sa.Column("estudio_id", sa.Integer(), nullable=True),
        schema="core",
    )
    op.create_index(
        "ix_core_companies_estudio_id", "companies", ["estudio_id"], schema="core"
    )
    op.create_foreign_key(
        "fk_companies_estudio_id",
        "companies",
        "estudios",
        ["estudio_id"],
        ["id"],
        source_schema="core",
        referent_schema="core",
        ondelete="RESTRICT",
    )

    _backfill(op.get_bind())


def _backfill(conn) -> None:
    """Crea un estudio por dueño y enlaza cada empresa a él."""
    admin_role_id = conn.execute(
        sa.text("SELECT id FROM core.roles WHERE key = 'admin_empresa'")
    ).scalar()

    companies = conn.execute(
        sa.text("SELECT id FROM core.companies ORDER BY id")
    ).fetchall()

    for (company_id,) in companies:
        # Dueño: la membresía admin más antigua; si no hay admin, la más antigua.
        # (role_id = :admin) ordena las admin primero; NULL cuando falta el rol.
        owner_id = conn.execute(
            sa.text(
                "SELECT user_id FROM core.memberships "
                "WHERE company_id = :cid AND status = 'activo' "
                "ORDER BY (role_id = :admin) DESC, id ASC LIMIT 1"
            ),
            {"cid": company_id, "admin": admin_role_id},
        ).scalar()
        if owner_id is None:
            # Empresa sin miembros activos: se deja sin estudio para revisión.
            continue

        estudio_id = conn.execute(
            sa.text("SELECT id FROM core.estudios WHERE owner_user_id = :u"),
            {"u": owner_id},
        ).scalar()
        if estudio_id is None:
            nombre = conn.execute(
                sa.text("SELECT nombre FROM core.users WHERE id = :u"),
                {"u": owner_id},
            ).scalar()
            estudio_id = conn.execute(
                sa.text(
                    "INSERT INTO core.estudios (nombre, owner_user_id, created_at, updated_at) "
                    "VALUES (:n, :u, now(), now()) RETURNING id"
                ),
                {"n": f"Estudio {nombre or owner_id}", "u": owner_id},
            ).scalar()

        conn.execute(
            sa.text("UPDATE core.companies SET estudio_id = :e WHERE id = :c"),
            {"e": estudio_id, "c": company_id},
        )


def downgrade() -> None:
    op.drop_constraint(
        "fk_companies_estudio_id", "companies", schema="core", type_="foreignkey"
    )
    op.drop_index("ix_core_companies_estudio_id", "companies", schema="core")
    op.drop_column("companies", "estudio_id", schema="core")
    op.drop_index("ix_core_estudios_owner_user_id", "estudios", schema="core")
    op.drop_table("estudios", schema="core")
