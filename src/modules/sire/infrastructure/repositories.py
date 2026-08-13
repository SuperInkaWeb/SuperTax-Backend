"""Repositorios SQLAlchemy del módulo SIRE (schema `sire`)."""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.sire.domain.entities import (
    JobStatus,
    ReconciliationJob,
    TipoLibro,
)
from src.modules.sire.infrastructure.models import (
    CompanyFileMappingModel,
    ReconciliationJobModel,
    ReconciliationResultModel,
    ReportFileModel,
    SireCredentialsModel,
)
from src.platform.database.base import utcnow

_TICKET_FRESCURA_HORAS = 24


class SqlReconciliationRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_by_company(
        self, company_id: int, limit: int, offset: int
    ) -> list[ReconciliationJob]:
        rows = self._db.scalars(
            select(ReconciliationJobModel)
            .where(ReconciliationJobModel.company_id == company_id)
            .order_by(ReconciliationJobModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [self._to_entity(row) for row in rows]

    def get(self, job_id: int, company_id: int) -> ReconciliationJob | None:
        row = self._db.scalar(
            select(ReconciliationJobModel).where(
                ReconciliationJobModel.id == job_id,
                ReconciliationJobModel.company_id == company_id,
            )
        )
        return self._to_entity(row) if row else None

    def create(
        self,
        company_id: int,
        user_id: int,
        periodo: str,
        tipo_libro: TipoLibro,
        filename: str | None,
        sin_sire: bool = False,
        mapeo_config: dict | None = None,
        cobertura_fechas: list | None = None,
        reutilizar_propuesta: bool = False,
    ) -> ReconciliationJob:
        row = ReconciliationJobModel(
            company_id=company_id,
            created_by_id=user_id,
            periodo=periodo,
            tipo_libro=tipo_libro,
            status=JobStatus.en_cola,
            empresa_filename=filename,
            sin_sire=sin_sire,
            mapeo_config=mapeo_config,
            cobertura_fechas=cobertura_fechas,
            reutilizar_propuesta=reutilizar_propuesta,
        )
        self._db.add(row)
        self._db.commit()
        self._db.refresh(row)
        return self._to_entity(row)

    def buscar_ticket_fresco(
        self,
        company_id: int,
        periodo: str,
        tipo_libro: TipoLibro,
        exclude_job_id: int | None = None,
    ) -> ReconciliationJobModel | None:
        """Último job de la empresa con ticket del mismo periodo/libro y < 24h."""
        limite = utcnow() - timedelta(hours=_TICKET_FRESCURA_HORAS)
        condiciones = [
            ReconciliationJobModel.company_id == company_id,
            ReconciliationJobModel.periodo == periodo,
            ReconciliationJobModel.tipo_libro == tipo_libro,
            ReconciliationJobModel.num_ticket.is_not(None),
            ReconciliationJobModel.created_at > limite,
        ]
        if exclude_job_id is not None:
            condiciones.append(ReconciliationJobModel.id != exclude_job_id)
        return self._db.scalar(
            select(ReconciliationJobModel)
            .where(*condiciones)
            .order_by(ReconciliationJobModel.id.desc())
        )

    def buscar_tickets_frescos_multi(
        self,
        company_id: int,
        periodos: list[str],
        tipo_libro: TipoLibro,
        exclude_job_id: int | None = None,
    ) -> dict[str, str]:
        """Compras 'sin SIRE': {periodo: num_ticket} fresco (<24h) por cada periodo."""
        if not periodos:
            return {}
        limite = utcnow() - timedelta(hours=_TICKET_FRESCURA_HORAS)
        condiciones = [
            ReconciliationJobModel.company_id == company_id,
            ReconciliationJobModel.periodo.in_(periodos),
            ReconciliationJobModel.tipo_libro == tipo_libro,
            ReconciliationJobModel.num_ticket.is_not(None),
            ReconciliationJobModel.created_at > limite,
        ]
        if exclude_job_id is not None:
            condiciones.append(ReconciliationJobModel.id != exclude_job_id)
        rows = self._db.scalars(
            select(ReconciliationJobModel)
            .where(*condiciones)
            .order_by(ReconciliationJobModel.id.asc())
        ).all()
        # Ascendente: el más reciente (id mayor) sobrescribe → gana el último.
        return {row.periodo: row.num_ticket for row in rows if row.num_ticket}

    def set_file_path(self, job_id: int, storage_path: str) -> None:
        row = self._db.get(ReconciliationJobModel, job_id)
        if row is not None:
            row.empresa_file_path = storage_path
            self._db.commit()

    def requeue_failed(self, job_id: int, company_id: int) -> ReconciliationJob | None:
        """Reencola un job en error para que el worker lo reprocese. None si no existe."""
        row = self._db.scalar(
            select(ReconciliationJobModel).where(
                ReconciliationJobModel.id == job_id,
                ReconciliationJobModel.company_id == company_id,
            )
        )
        if row is None:
            return None
        if row.status != JobStatus.error:
            raise ValueError("Solo se pueden reanudar conciliaciones en estado de error")
        if not row.empresa_file_path:
            raise ValueError("Este job no es reanudable (no se conservó el archivo)")
        row.status = JobStatus.en_cola
        row.error_message = None
        self._db.commit()
        return self._to_entity(row)

    def mark_error(self, job_id: int, message: str) -> None:
        row = self._db.get(ReconciliationJobModel, job_id)
        if row is not None:
            row.status = JobStatus.error
            row.error_message = message[:1000]
            self._db.commit()

    def save_success(self, job_id: int, result: dict) -> None:
        """Persiste el resultado y el reporte, y marca el job como completado."""
        self._db.add(
            ReconciliationResultModel(
                job_id=job_id,
                escenario_a_count=result["escenario_a_count"],
                escenario_b_count=result["escenario_b_count"],
                escenario_c_count=result["escenario_c_count"],
                escenario_d_count=result["escenario_d_count"],
                igv_diferencia_total=result["igv_diferencia_total"],
                tiene_alertas_rojas=result["tiene_alertas_rojas"],
            )
        )
        self._db.add(
            ReportFileModel(
                job_id=job_id,
                filename=result["filename_xlsx"],
                storage_path=result["path_xlsx"],
                file_size_bytes=result["excel_size"],
                csv_a_storage_path=result["path_csv_a"],
                csv_a_file_size_bytes=result["csv_a_size"],
                csv_b_storage_path=result["path_csv"],
                csv_b_file_size_bytes=result["csv_b_size"],
                csv_c_storage_path=result["path_csv_c"],
                csv_c_file_size_bytes=result["csv_c_size"],
                csv_d_storage_path=result["path_csv_d"],
                csv_d_file_size_bytes=result["csv_d_size"],
            )
        )
        row = self._db.get(ReconciliationJobModel, job_id)
        if row is not None:
            row.status = JobStatus.completado
            row.completed_at = utcnow()
            row.empresa_file_path = None
        self._db.commit()

    def get_report(self, job_id: int, company_id: int) -> ReportFileModel | None:
        """Devuelve el ReportFile del job, validando que sea de la empresa activa."""
        return self._db.scalar(
            select(ReportFileModel)
            .join(
                ReconciliationJobModel,
                ReconciliationJobModel.id == ReportFileModel.job_id,
            )
            .where(
                ReportFileModel.job_id == job_id,
                ReconciliationJobModel.company_id == company_id,
            )
        )

    def _to_entity(self, row: ReconciliationJobModel) -> ReconciliationJob:
        result = self._db.scalar(
            select(ReconciliationResultModel).where(
                ReconciliationResultModel.job_id == row.id
            )
        )
        report = self._db.scalar(
            select(ReportFileModel).where(ReportFileModel.job_id == row.id)
        )
        return ReconciliationJob(
            id=row.id,
            company_id=row.company_id,
            periodo=row.periodo,
            tipo_libro=row.tipo_libro,
            status=row.status,
            created_at=row.created_at,
            empresa_filename=row.empresa_filename,
            completed_at=row.completed_at,
            error_message=row.error_message,
            igv_diferencia_total=(
                float(result.igv_diferencia_total) if result else None
            ),
            tiene_alertas_rojas=result.tiene_alertas_rojas if result else None,
            has_report=report is not None,
            has_csv_a=report is not None and report.csv_a_storage_path is not None,
            has_csv_b=report is not None and report.csv_b_storage_path is not None,
            has_csv_c=report is not None and report.csv_c_storage_path is not None,
            has_csv_d=report is not None and report.csv_d_storage_path is not None,
        )


class SqlCredentialsRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, company_id: int) -> SireCredentialsModel | None:
        return self._db.scalar(
            select(SireCredentialsModel).where(
                SireCredentialsModel.company_id == company_id
            )
        )

    def upsert(
        self,
        company_id: int,
        updated_by_id: int,
        usuario_sol: str,
        clave_sol_enc: str,
        client_id: str,
        client_secret_enc: str,
    ) -> None:
        creds = self.get(company_id)
        if creds is None:
            creds = SireCredentialsModel(company_id=company_id)
            self._db.add(creds)
        creds.usuario_sol = usuario_sol
        creds.clave_sol_enc = clave_sol_enc
        creds.client_id = client_id
        creds.client_secret_enc = client_secret_enc
        creds.updated_by_id = updated_by_id
        self._db.commit()


class SqlFileMappingRepository:
    def __init__(self, db: Session) -> None:
        self._db = db

    def get(self, company_id: int, tipo_libro: str) -> CompanyFileMappingModel | None:
        return self._db.scalar(
            select(CompanyFileMappingModel).where(
                CompanyFileMappingModel.company_id == company_id,
                CompanyFileMappingModel.tipo_libro == tipo_libro,
            )
        )

    def save(self, company_id: int, tipo_libro: str, cfg: dict) -> CompanyFileMappingModel:
        mapping = self.get(company_id, tipo_libro)
        if mapping is None:
            mapping = CompanyFileMappingModel(company_id=company_id, tipo_libro=tipo_libro)
            self._db.add(mapping)
        mapping.delimiter = cfg.get("delimiter", "|")
        mapping.encoding = cfg.get("encoding", "latin-1")
        mapping.has_header = bool(cfg.get("has_header", False))
        mapping.skip_rows = int(cfg.get("skip_rows", 0))
        mapping.columnas = cfg.get("columnas") or {}
        mapping.serie_numero_combinado = bool(cfg.get("serie_numero_combinado", False))
        mapping.confirmed_by_user = True
        self._db.commit()
        self._db.refresh(mapping)
        return mapping

    def delete(self, company_id: int, tipo_libro: str) -> None:
        mapping = self.get(company_id, tipo_libro)
        if mapping is not None:
            self._db.delete(mapping)
            self._db.commit()

    def get_config(self, company_id: int, tipo_libro: str) -> dict | None:
        """Formato guardado y confirmado como dict plano (para el parser), o None."""
        m = self.get(company_id, tipo_libro)
        if m and m.columnas and m.confirmed_by_user:
            return {
                "delimiter": m.delimiter,
                "encoding": m.encoding,
                "has_header": m.has_header,
                "skip_rows": m.skip_rows,
                "serie_numero_combinado": m.serie_numero_combinado,
                "columnas": m.columnas,
            }
        return None
