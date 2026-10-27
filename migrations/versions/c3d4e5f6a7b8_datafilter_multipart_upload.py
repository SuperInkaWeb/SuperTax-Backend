"""datafilter: columna multipart_upload_id para subida directa a R2

Revision ID: c3d4e5f6a7b8
Revises: b7c8d9e0f1a2
Create Date: 2026-10-26 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b7c8d9e0f1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("multipart_upload_id", sa.String(length=255), nullable=True),
        schema="datafilter",
    )


def downgrade() -> None:
    op.drop_column("datasets", "multipart_upload_id", schema="datafilter")
