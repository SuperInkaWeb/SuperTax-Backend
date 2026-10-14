"""correos: schema + tablas del módulo de automatización de correos

Revision ID: a1b2c3d4e5f6
Revises: e1f2a3b4c5d6
Create Date: 2026-10-13 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "e1f2a3b4c5d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS correos")

    op.create_table(
        "mailbox_connections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("account_email", sa.String(length=320), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["core.companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["core.users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id", "created_by_id", "provider", name="uq_correos_mailbox_company_user_prov"
        ),
        schema="correos",
    )
    op.create_index(
        "ix_correos_mailbox_company", "mailbox_connections", ["company_id"], schema="correos"
    )
    op.create_index(
        "ix_correos_mailbox_status", "mailbox_connections", ["status"], schema="correos"
    )

    op.create_table(
        "scan_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), nullable=True),
        sa.Column("connection_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("phase", sa.String(length=20), nullable=False),
        sa.Column("progress_pct", sa.Integer(), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("counters", sa.JSON(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["core.companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["core.users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["connection_id"], ["correos.mailbox_connections.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "idempotency_key", name="uq_correos_scan_idempotency"),
        schema="correos",
    )
    op.create_index("ix_correos_scan_company", "scan_jobs", ["company_id"], schema="correos")
    op.create_index("ix_correos_scan_status", "scan_jobs", ["status"], schema="correos")

    op.create_table(
        "email_messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_id", sa.String(length=255), nullable=False),
        sa.Column("sender", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["core.companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["correos.scan_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id", "provider", "provider_id", name="uq_correos_msg_company_prov_id"
        ),
        schema="correos",
    )
    op.create_index("ix_correos_msg_company", "email_messages", ["company_id"], schema="correos")
    op.create_index("ix_correos_msg_job", "email_messages", ["job_id"], schema="correos")

    op.create_table(
        "attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("antivirus_status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["core.companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["message_id"], ["correos.email_messages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "sha256", name="uq_correos_attach_company_sha"),
        schema="correos",
    )
    op.create_index("ix_correos_attach_company", "attachments", ["company_id"], schema="correos")
    op.create_index("ix_correos_attach_message", "attachments", ["message_id"], schema="correos")

    op.create_table(
        "extracted_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("attachment_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("profile", sa.String(length=64), nullable=False),
        sa.Column("ruc_contribuyente", sa.String(length=11), nullable=True),
        sa.Column("nombre_contribuyente", sa.String(length=120), nullable=False),
        sa.Column("ruc_inquilino", sa.String(length=11), nullable=True),
        sa.Column("nombre_inquilino", sa.String(length=120), nullable=False),
        sa.Column("tipo_doc_inquilino", sa.String(length=40), nullable=False),
        sa.Column("tipo_de_bien", sa.String(length=40), nullable=False),
        sa.Column("periodo", sa.String(length=6), nullable=True),
        sa.Column("fecha_de_pago", sa.Date(), nullable=True),
        sa.Column("numero_de_operacion", sa.String(length=40), nullable=True),
        sa.Column("monto_alquiler", sa.Numeric(14, 2), nullable=True),
        sa.Column("tributo_resultante", sa.Numeric(14, 2), nullable=True),
        sa.Column("importe_pagado", sa.Numeric(14, 2), nullable=True),
        sa.Column("intereses_moratorios", sa.Numeric(14, 2), nullable=True),
        sa.Column("moneda", sa.String(length=3), nullable=False),
        sa.Column("campos_crudos", sa.JSON(), nullable=False),
        sa.Column("confianza_por_campo", sa.JSON(), nullable=False),
        sa.Column("completitud", sa.String(length=20), nullable=False),
        sa.Column("estado_de_revision", sa.String(length=20), nullable=False),
        sa.Column("revisado_por_id", sa.Integer(), nullable=True),
        sa.Column("revisado_en", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estrategia_usada", sa.String(length=32), nullable=True),
        sa.Column("duracion_ms", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["core.companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["attachment_id"], ["correos.attachments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["job_id"], ["correos.scan_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revisado_por_id"], ["core.users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "company_id", "attachment_id", name="uq_correos_record_company_attach"
        ),
        schema="correos",
    )
    op.create_index(
        "ix_correos_record_company_created", "extracted_records",
        ["company_id", "created_at"], schema="correos",
    )
    op.create_index(
        "ix_correos_record_ruc_periodo", "extracted_records",
        ["company_id", "ruc_contribuyente", "periodo"], schema="correos",
    )
    op.create_index(
        "ix_correos_record_review", "extracted_records",
        ["company_id", "estado_de_revision"], schema="correos",
    )
    op.create_index("ix_correos_record_job", "extracted_records", ["job_id"], schema="correos")

    op.create_table(
        "processing_errors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("retryable", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["core.companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["correos.scan_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        schema="correos",
    )
    op.create_index(
        "ix_correos_errors_company", "processing_errors", ["company_id"], schema="correos"
    )
    op.create_index("ix_correos_errors_job", "processing_errors", ["job_id"], schema="correos")

    op.create_table(
        "report_exports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("requested_by_id", sa.Integer(), nullable=True),
        sa.Column("format", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("filters", sa.JSON(), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["core.companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["requested_by_id"], ["core.users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        schema="correos",
    )
    op.create_index(
        "ix_correos_exports_company", "report_exports", ["company_id"], schema="correos"
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_ip", sa.String(length=64), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("audit_metadata", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["core.companies.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["core.users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        schema="correos",
    )
    op.create_index(
        "ix_correos_audit_company_occurred", "audit_log",
        ["company_id", "occurred_at"], schema="correos",
    )


def downgrade() -> None:
    op.drop_table("audit_log", schema="correos")
    op.drop_table("report_exports", schema="correos")
    op.drop_table("processing_errors", schema="correos")
    op.drop_table("extracted_records", schema="correos")
    op.drop_table("attachments", schema="correos")
    op.drop_table("email_messages", schema="correos")
    op.drop_table("scan_jobs", schema="correos")
    op.drop_table("mailbox_connections", schema="correos")
    op.execute("DROP SCHEMA IF EXISTS correos")
