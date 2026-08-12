"""Puerto de almacenamiento de archivos."""
from abc import ABC, abstractmethod


class FileStorage(ABC):
    @abstractmethod
    def save(self, path: str, content: bytes) -> str:
        """Guarda el archivo y devuelve el storage_path."""

    @abstractmethod
    def open_write(self, path: str):
        """
        Context manager que entrega un archivo binario para escribir en streaming
        (reportes grandes sin cargarlos enteros en RAM). Al cerrarse, persiste.
        """

    @abstractmethod
    def size(self, storage_path: str) -> int:
        """Tamaño en bytes del archivo, sin leerlo."""

    @abstractmethod
    def read(self, storage_path: str) -> bytes:
        """Lee y devuelve el contenido del archivo."""

    @abstractmethod
    def delete(self, storage_path: str) -> None:
        """Elimina el archivo si existe."""

    @abstractmethod
    def exists(self, storage_path: str) -> bool:
        """Indica si el archivo existe, sin leerlo."""
