"""
Casos de uso del módulo SIRE.

Orquestan el dominio a través de sus puertos (no conocen SQLAlchemy ni FastAPI).
La creación/procesamiento de conciliaciones (motor SUNAT + worker) se incorpora
en el sub-paso 2b; aquí está el lado de lectura.
"""
from src.modules.sire.domain.entities import ReconciliationJob
from src.modules.sire.domain.ports import ReconciliationRepository

_LIMIT_MAX = 200


def list_reconciliation_jobs(
    repo: ReconciliationRepository, company_id: int, limit: int, offset: int
) -> list[ReconciliationJob]:
    limit = max(1, min(limit, _LIMIT_MAX))
    offset = max(0, offset)
    return repo.list_by_company(company_id, limit, offset)


def get_reconciliation_job(
    repo: ReconciliationRepository, job_id: int, company_id: int
) -> ReconciliationJob | None:
    return repo.get(job_id, company_id)
