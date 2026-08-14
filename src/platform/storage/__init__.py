"""
Abstracción de almacenamiento de archivos (puerto + selección de adaptador).

El adaptador se elige por `STORAGE_BACKEND`: "local" (disco, dev) o "s3"
(AWS S3 / Cloudflare R2 / MinIO). Los módulos dependen solo de la interfaz
`FileStorage`, así cambiar de backend no los toca.
"""
from src.platform.config.settings import settings
from src.platform.storage.base import FileStorage
from src.platform.storage.local import LocalStorage


def _crear_storage() -> FileStorage:
    if settings.STORAGE_BACKEND == "s3":
        # Import perezoso: boto3 solo se carga si realmente se usa S3.
        from src.platform.storage.s3 import S3Storage

        return S3Storage()
    return LocalStorage()


storage: FileStorage = _crear_storage()


def get_storage() -> FileStorage:
    """Dependencia de FastAPI: entrega la implementación de storage activa."""
    return storage
