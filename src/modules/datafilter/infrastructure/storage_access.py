"""
Acceso de bajo nivel al storage para DuckDB (locators + descargas puntuales).

DuckDB lee/escribe por un *locator*: una ruta local o una URI `s3://bucket/clave`
(R2/S3 vía httpfs). Aquí se construyen esos locators sobre el storage de la
plataforma y se bajan copias locales cuando hace falta (Excel, transcodificación).

Las claves las genera el servidor (`company_id` + `dataset_id`), nunca datos del
usuario, y se anclan bajo `datafilter/<company_id>/<dataset_id>/`.
"""
import os
import tempfile

import boto3
from botocore.config import Config

from src.platform.config.settings import settings

_PREFIX = "datafilter"


def usa_s3() -> bool:
    return settings.STORAGE_BACKEND == "s3"


def source_key(company_id: int, dataset_id: int, extension: str) -> str:
    """Clave del archivo original subido."""
    ext = extension.lstrip(".").lower()
    return f"{_PREFIX}/{company_id}/{dataset_id}/source.{ext}"


def parquet_key(company_id: int, dataset_id: int) -> str:
    """Clave del Parquet resultante."""
    return f"{_PREFIX}/{company_id}/{dataset_id}/data.parquet"


def locator(key: str) -> str:
    """Locator para DuckDB: `s3://bucket/clave` (R2) o ruta local absoluta."""
    if usa_s3():
        return f"s3://{settings.S3_BUCKET}/{key}"
    base = os.path.abspath(settings.STORAGE_LOCAL_PATH)
    return os.path.join(base, *key.split("/"))


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL or None,
        aws_access_key_id=settings.S3_ACCESS_KEY or None,
        aws_secret_access_key=settings.S3_SECRET_KEY or None,
        region_name=settings.S3_REGION or None,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def download_to_temp(key: str, suffix: str) -> str:
    """Descarga el objeto a un archivo temporal local y devuelve su ruta.

    En modo local no copia: devuelve la ruta real del archivo (que el llamador
    NO debe borrar; por eso `es_temporal` distingue los dos casos)."""
    if not usa_s3():
        return locator(key)
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    handle.close()
    _s3_client().download_file(settings.S3_BUCKET, key, handle.name)
    return handle.name


def es_temporal(ruta: str) -> bool:
    """True si `ruta` es una descarga temporal (solo pasa en modo S3)."""
    return usa_s3()


def read_prefix(key: str, length: int) -> bytes:
    """Primeros `length` bytes del archivo (para detectar BOM sin bajarlo entero)."""
    if not usa_s3():
        with open(locator(key), "rb") as raw:
            return raw.read(length)
    obj = _s3_client().get_object(
        Bucket=settings.S3_BUCKET, Key=key, Range=f"bytes=0-{length - 1}"
    )
    return obj["Body"].read()


def delete(key: str) -> None:
    """Borra el objeto (best-effort en el llamador)."""
    if usa_s3():
        _s3_client().delete_object(Bucket=settings.S3_BUCKET, Key=key)
    else:
        ruta = locator(key)
        if os.path.exists(ruta):
            os.remove(ruta)
