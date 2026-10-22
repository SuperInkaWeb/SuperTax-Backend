"""core: bóveda única de credenciales por empresa (consolida SOL de SUNAT y SIRE)

Revision ID: b7c8d9e0f1a2
Revises: a1b2c3d4e5f6
Create Date: 2026-10-21 00:00:00.000000
"""
import logging
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c8d9e0f1a2"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_log = logging.getLogger("alembic.credentials")


def upgrade() -> None:
    op.create_table(
        "company_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("identifier", sa.String(length=100), nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["core.companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["core.users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "kind", name="uq_company_credential_kind"),
        schema="core",
    )
    op.create_index(
        "ix_company_credentials_company",
        "company_credentials",
        ["company_id"],
        schema="core",
    )

    _backfill_desde_legacy()


def _backfill_desde_legacy() -> None:
    """
    Copia las credenciales existentes de los módulos a la bóveda única.

    Best-effort y no fatal: si falla (p. ej. falta la clave de cifrado en un paso
    de despliegue aislado), el esquema queda creado y las lecturas siguen cayendo
    al almacén legacy hasta el siguiente guardado. Idempotente vía ON CONFLICT.
    """
    conn = op.get_bind()
    try:
        # SIRE guarda identificadores en claro → copia directa (sin cifrar/descifrar).
        conn.execute(
            sa.text(
                "INSERT INTO core.company_credentials "
                "(company_id, kind, identifier, secret_enc, updated_at) "
                "SELECT company_id, 'sol', usuario_sol, clave_sol_enc, now() "
                "FROM sire.company_credentials "
                "ON CONFLICT (company_id, kind) DO NOTHING"
            )
        )
        conn.execute(
            sa.text(
                "INSERT INTO core.company_credentials "
                "(company_id, kind, identifier, secret_enc, updated_at) "
                "SELECT company_id, 'sire_api', client_id, client_secret_enc, now() "
                "FROM sire.company_credentials "
                "ON CONFLICT (company_id, kind) DO NOTHING"
            )
        )

        # SUNAT cifra el usuario SOL → hay que descifrarlo para guardarlo como
        # identificador en claro. El secreto (clave) ya está cifrado con la misma
        # clave Fernet, así que se copia tal cual. Prioriza el 'sol' de SIRE si ya
        # existe (DO NOTHING): ambos valores son el mismo usuario/clave SOL.
        from src.platform.security import decrypt_field  # import diferido: clave de entorno

        filas = conn.execute(
            sa.text(
                "SELECT company_id, usuario_enc, clave_enc "
                "FROM sunat.sunat_credentials"
            )
        ).fetchall()
        for fila in filas:
            conn.execute(
                sa.text(
                    "INSERT INTO core.company_credentials "
                    "(company_id, kind, identifier, secret_enc, updated_at) "
                    "VALUES (:cid, 'sol', :identifier, :secret_enc, now()) "
                    "ON CONFLICT (company_id, kind) DO NOTHING"
                ),
                {
                    "cid": fila.company_id,
                    "identifier": decrypt_field(fila.usuario_enc),
                    "secret_enc": fila.clave_enc,
                },
            )
    except Exception:  # noqa: BLE001 — el backfill nunca debe abortar la migración
        _log.exception(
            "Backfill de credenciales omitido; se poblará al próximo guardado"
        )


def downgrade() -> None:
    op.drop_index(
        "ix_company_credentials_company", "company_credentials", schema="core"
    )
    op.drop_table("company_credentials", schema="core")
