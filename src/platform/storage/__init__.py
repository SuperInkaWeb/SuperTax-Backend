"""
Abstracción de almacenamiento de archivos (puerto + selección de adaptador).

Hoy usa disco local; al migrar a S3/R2 solo se cambia el adaptador aquí, sin
tocar a los módulos que dependen de la interfaz `FileStorage`.
"""
from src.platform.storage.base import FileStorage
from src.platform.storage.local import LocalStorage

storage: FileStorage = LocalStorage()


def get_storage() -> FileStorage:
    """Dependencia de FastAPI: entrega la implementación de storage activa."""
    return storage
