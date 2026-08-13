"""
Modelos de persistencia del módulo SIRE (schema `sire`).

Referencian a las tablas del núcleo (`core.companies`, `core.users`) solo por
su id — el módulo no duplica datos de identidad ni de empresa.
"""
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from src.modules.sire.domain.entities import JobStatus, TipoLibro
from src.platform.database.base import Base, utcnow


class ReconciliationJobModel(Base):
    __tablename__ = "reconciliation_jobs"
    __table_args__ = {"schema": "sire"}

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    created_by_id: Mapped[int] = mapped_column(ForeignKey("core.users.id"))
    periodo: Mapped[str] = mapped_column(String(6))
    tipo_libro: Mapped[TipoLibro] = mapped_column(Enum(TipoLibro, name="sire_tipo_libro"))
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, name="sire_job_status"), default=JobStatus.en_cola
    )
    empresa_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    empresa_file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Compras: la empresa no está afiliada al SIRE (comprobantes rezagados en A).
    sin_sire: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Reutilizar una propuesta SUNAT fresca de otro job en vez de solicitar una nueva.
    reutilizar_propuesta: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Mapeo de columnas usado por ESTE job (para reanudar con el mismo parseo).
    mapeo_config: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Compras "sin SIRE": tickets SUNAT de propuestas de meses anteriores que
    # este job generó ({ "AAAAMM": "numTicket" }), para reaprovechar al reanudar.
    extra_tickets: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Ventas: fechas AAAA-MM-DD que el archivo declara cubrir ([] = mes completo).
    cobertura_fechas: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Ticket SUNAT de la propuesta (para reanudar/mostrar origen).
    num_ticket: Mapped[str | None] = mapped_column(String(30), nullable=True)
    propuesta_origen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ReconciliationResultModel(Base):
    __tablename__ = "reconciliation_results"
    __table_args__ = {"schema": "sire"}

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("sire.reconciliation_jobs.id", ondelete="CASCADE"), unique=True
    )
    escenario_a_count: Mapped[int] = mapped_column(Integer, default=0)
    escenario_b_count: Mapped[int] = mapped_column(Integer, default=0)
    escenario_c_count: Mapped[int] = mapped_column(Integer, default=0)
    escenario_d_count: Mapped[int] = mapped_column(Integer, default=0)
    igv_diferencia_total: Mapped[float] = mapped_column(Numeric(14, 2), default=0)
    tiene_alertas_rojas: Mapped[bool] = mapped_column(Boolean, default=False)


class ReportFileModel(Base):
    """Archivos generados por la conciliación (Excel + CSV por escenario grande)."""

    __tablename__ = "report_files"
    __table_args__ = {"schema": "sire"}

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("sire.reconciliation_jobs.id", ondelete="CASCADE"), unique=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    csv_a_storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    csv_a_file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    csv_b_storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    csv_b_file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    csv_c_storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    csv_c_file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    csv_d_storage_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    csv_d_file_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SireCredentialsModel(Base):
    """
    Credenciales SUNAT-SIRE de la empresa. Los campos sensibles (clave SOL y
    client_secret) se guardan cifrados con Fernet — nunca en texto plano.
    """

    __tablename__ = "company_credentials"
    __table_args__ = {"schema": "sire"}

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), unique=True
    )
    usuario_sol: Mapped[str] = mapped_column(String(50))
    clave_sol_enc: Mapped[str] = mapped_column(Text)
    client_id: Mapped[str] = mapped_column(String(100))
    client_secret_enc: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    updated_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("core.users.id"), nullable=True
    )


class CompanyFileMappingModel(Base):
    """
    Formato de archivo TXT/CSV guardado por la empresa (por libro). Una vez
    confirmado, el parser lo usa directamente sin re-detectar columnas.
    """

    __tablename__ = "company_file_mappings"
    __table_args__ = (
        UniqueConstraint("company_id", "tipo_libro", name="uq_file_mapping_company_libro"),
        {"schema": "sire"},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(
        ForeignKey("core.companies.id", ondelete="CASCADE"), index=True
    )
    tipo_libro: Mapped[str] = mapped_column(String(10), default="ventas")
    delimiter: Mapped[str] = mapped_column(String(5), default="|")
    encoding: Mapped[str] = mapped_column(String(20), default="latin-1")
    has_header: Mapped[bool] = mapped_column(Boolean, default=False)
    skip_rows: Mapped[int] = mapped_column(Integer, default=0)
    columnas: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    serie_numero_combinado: Mapped[bool] = mapped_column(Boolean, default=False)
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
