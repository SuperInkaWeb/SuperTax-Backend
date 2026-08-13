"""
Casos de uso del módulo SIRE.

Orquestan el dominio a través de sus puertos (no conocen SQLAlchemy ni FastAPI).
El procesamiento de la conciliación (motor SUNAT + worker) se incorpora en el
sub-paso 2b-2; aquí están la lectura y la creación del job.
"""
from src.modules.sire.domain.entities import ReconciliationJob, TipoLibro
from src.modules.sire.domain.ports import ReconciliationRepository
from src.platform.storage.base import FileStorage

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


def _validar_periodo(periodo: str) -> None:
    if len(periodo) != 6 or not periodo.isdigit():
        raise ValueError("El periodo debe tener formato AAAAMM (ej. 202601)")
    year, month = int(periodo[:4]), int(periodo[4:])
    if not (2020 <= year <= 2099) or not (1 <= month <= 12):
        raise ValueError("Periodo inválido")


def create_reconciliation_job(
    repo: ReconciliationRepository,
    storage: FileStorage,
    company_id: int,
    user_id: int,
    periodo: str,
    tipo_libro: TipoLibro,
    filename: str | None,
    content: bytes,
    sin_sire: bool = False,
    mapeo_config: dict | None = None,
    cobertura_fechas: list | None = None,
    reutilizar_propuesta: bool = False,
) -> ReconciliationJob:
    """
    Valida el periodo, crea el job en estado 'en_cola' y guarda el archivo de la
    empresa. El procesamiento asíncrono lo tomará el worker.

    - sin_sire solo aplica a compras.
    - mapeo_config: parseo manual de columnas para ESTE job (opcional).
    - cobertura_fechas: solo ventas.
    - reutilizar_propuesta: aprovechar una propuesta fresca de otro job si existe.
    """
    _validar_periodo(periodo)
    if not content:
        raise ValueError("El archivo está vacío")

    sin_sire = bool(sin_sire) and tipo_libro == TipoLibro.compras
    job = repo.create(
        company_id,
        user_id,
        periodo,
        tipo_libro,
        filename,
        sin_sire=sin_sire,
        mapeo_config=mapeo_config,
        cobertura_fechas=cobertura_fechas,
        reutilizar_propuesta=reutilizar_propuesta,
    )
    storage_path = f"sire/uploads/{company_id}/{job.id}/{filename or 'empresa.csv'}"
    storage.save(storage_path, content)
    repo.set_file_path(job.id, storage_path)
    return job
