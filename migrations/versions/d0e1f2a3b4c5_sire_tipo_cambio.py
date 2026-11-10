"""sire: tabla global de tipo de cambio (una fila por día calendario)

Requerimiento 2 (SIRE): compara el TC del PLE contra el TC oficial. La tabla es
GLOBAL (el TC es nacional, no por empresa) y guarda el valor en la forma que
publica SUNAT (tcS01Alias): una fila por día calendario, compra y venta. Se
puebla desde el BCRP (serie SBS) normalizando el desfase de +1 día hábil.

Sin backfill de datos aquí: la tabla nace vacía y se puebla desde el BCRP (seed
o refresco on-demand al conciliar).

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-11-10 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tipo_cambio",
        sa.Column("fecha", sa.Date(), primary_key=True),
        sa.Column("compra", sa.Numeric(7, 3), nullable=False),
        sa.Column("venta", sa.Numeric(7, 3), nullable=False),
        sa.Column("fuente", sa.String(20), nullable=False, server_default="bcrp"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        schema="sire",
    )


def downgrade() -> None:
    op.drop_table("tipo_cambio", schema="sire")
