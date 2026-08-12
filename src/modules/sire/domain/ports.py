"""Puertos del dominio SIRE: interfaces que la infraestructura implementa."""
from typing import Protocol

from src.modules.sire.domain.entities import ReconciliationJob


class ReconciliationRepository(Protocol):
    def list_by_company(
        self, company_id: int, limit: int, offset: int
    ) -> list[ReconciliationJob]: ...

    def get(self, job_id: int, company_id: int) -> ReconciliationJob | None: ...
