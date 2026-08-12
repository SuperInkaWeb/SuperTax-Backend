"""
Adaptador de almacenamiento en disco local (desarrollo).

Incluye protección contra Path Traversal (defense-in-depth) y, en Linux, suelta
del page cache los archivos grandes para no inflar la RAM medida del contenedor.
"""
import os
from contextlib import contextmanager
from pathlib import Path

from src.platform.config.settings import settings
from src.platform.storage.base import FileStorage


def _soltar_cache(fd: int, flush: bool = False) -> None:
    """Pide al kernel soltar del page cache las páginas del archivo (solo Linux)."""
    if not hasattr(os, "posix_fadvise"):
        return
    try:
        if flush:
            os.fsync(fd)
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except OSError:
        pass


class LocalStorage(FileStorage):
    def __init__(self, base_path: str | None = None) -> None:
        self.base_path = Path(base_path or settings.STORAGE_LOCAL_PATH)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _ruta_segura(self, storage_path: str) -> Path:
        base = self.base_path.resolve()
        full = (base / storage_path).resolve()
        if not full.is_relative_to(base):
            raise ValueError(f"Ruta fuera del almacenamiento permitido: {storage_path!r}")
        return full

    def save(self, path: str, content: bytes) -> str:
        full_path = self._ruta_segura(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(content)
            f.flush()
            _soltar_cache(f.fileno(), flush=True)
        return str(path)

    @contextmanager
    def open_write(self, path: str):
        full_path = self._ruta_segura(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        f = open(full_path, "wb")
        try:
            yield f
        finally:
            f.flush()
            _soltar_cache(f.fileno(), flush=True)
            f.close()

    def size(self, storage_path: str) -> int:
        return self._ruta_segura(storage_path).stat().st_size

    def read(self, storage_path: str) -> bytes:
        with open(self._ruta_segura(storage_path), "rb") as f:
            data = f.read()
            _soltar_cache(f.fileno())
        return data

    def delete(self, storage_path: str) -> None:
        full_path = self._ruta_segura(storage_path)
        if full_path.exists():
            full_path.unlink()

    def exists(self, storage_path: str) -> bool:
        return self._ruta_segura(storage_path).exists()
