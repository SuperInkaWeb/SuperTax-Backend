"""datafilter: multipart_upload_id a Text (el UploadId de R2 supera 255 chars)

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-10-27 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "datasets",
        "multipart_upload_id",
        schema="datafilter",
        existing_type=sa.String(length=255),
        type_=sa.Text(),
        existing_nullable=True,
    )
    # Limpia los datasets que quedaron atascados en 'uploading' por el bug del tipo
    # de columna (el init fallaba al guardar el UploadId): nunca tuvieron archivo,
    # es seguro borrarlos para que no aparezcan colgados en la lista.
    op.execute("DELETE FROM datafilter.datasets WHERE status = 'uploading'")


def downgrade() -> None:
    op.alter_column(
        "datasets",
        "multipart_upload_id",
        schema="datafilter",
        existing_type=sa.Text(),
        type_=sa.String(length=255),
        existing_nullable=True,
    )
