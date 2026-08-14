"""
Adaptador de almacenamiento sobre S3 (compatible con AWS S3, Cloudflare R2 y
MinIO). Implementa el mismo puerto `FileStorage` que el adaptador local, así los
módulos no cambian al migrar.

Seguridad:
- El bucket debe ser PRIVADO. Las descargas pasan por la API, que valida token y
  empresa (aislamiento multi-tenant); nunca se expone el bucket públicamente.
- Credenciales solo desde variables de entorno (settings), nunca en código.
- Las keys se validan contra path traversal (defensa en profundidad).
"""
import tempfile
from contextlib import contextmanager

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from src.platform.config.settings import settings
from src.platform.storage.base import FileStorage

# Umbral tras el cual open_write derrama a disco en vez de RAM (reportes grandes).
_SPOOL_MAX_BYTES = 8 * 1024 * 1024

_CODIGOS_NO_ENCONTRADO = {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}


def _key_segura(storage_path: str) -> str:
    """Normaliza y rechaza rutas con traversal o absolutas (defensa en profundidad)."""
    limpia = storage_path.lstrip("/")
    if not limpia or ".." in limpia.split("/"):
        raise ValueError(f"Ruta de almacenamiento inválida: {storage_path!r}")
    return limpia


def _es_no_encontrado(exc: ClientError) -> bool:
    return exc.response.get("Error", {}).get("Code") in _CODIGOS_NO_ENCONTRADO


class S3Storage(FileStorage):
    def __init__(
        self,
        *,
        bucket: str | None = None,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
    ) -> None:
        # None = "usar settings"; un valor explícito (incluso "") lo sobreescribe.
        endpoint = settings.S3_ENDPOINT_URL if endpoint_url is None else endpoint_url
        clave = settings.S3_ACCESS_KEY if access_key is None else access_key
        secreto = settings.S3_SECRET_KEY if secret_key is None else secret_key

        self._bucket = bucket or settings.S3_BUCKET
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint or None,
            aws_access_key_id=clave or None,
            aws_secret_access_key=secreto or None,
            region_name=region or settings.S3_REGION,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},  # path-style: requerido por MinIO
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        )
        self._asegurar_bucket()

    def _asegurar_bucket(self) -> None:
        """Crea el bucket si no existe (comodidad para MinIO/dev)."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError as exc:
            if _es_no_encontrado(exc):
                self._client.create_bucket(Bucket=self._bucket)
            else:
                raise

    def save(self, path: str, content: bytes) -> str:
        self._client.put_object(Bucket=self._bucket, Key=_key_segura(path), Body=content)
        return path

    @contextmanager
    def open_write(self, path: str):
        key = _key_segura(path)
        # Buffer en RAM hasta el umbral, luego derrama a disco: soporta reportes
        # grandes sin cargarlos enteros en memoria. Se sube al cerrar.
        spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX_BYTES)
        try:
            yield spool
            spool.seek(0)
            self._client.upload_fileobj(spool, self._bucket, key)
        finally:
            spool.close()

    def size(self, storage_path: str) -> int:
        head = self._client.head_object(Bucket=self._bucket, Key=_key_segura(storage_path))
        return head["ContentLength"]

    def read(self, storage_path: str) -> bytes:
        obj = self._client.get_object(Bucket=self._bucket, Key=_key_segura(storage_path))
        return obj["Body"].read()

    def delete(self, storage_path: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=_key_segura(storage_path))

    def exists(self, storage_path: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=_key_segura(storage_path))
            return True
        except ClientError as exc:
            if _es_no_encontrado(exc):
                return False
            raise
