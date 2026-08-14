"""
Tests del adaptador S3 con `moto` (S3 simulado en memoria, sin MinIO ni red).

Cubre el ciclo de vida (save/read/size/exists/delete), la escritura en streaming
(open_write) y el rechazo de rutas con path traversal.
"""
import pytest
from moto import mock_aws

from src.platform.storage.s3 import S3Storage, _key_segura

_BUCKET = "plataforma-test"


def _storage() -> S3Storage:
    return S3Storage(
        bucket=_BUCKET,
        endpoint_url="",  # sin endpoint → moto intercepta el S3 de AWS
        access_key="test",
        secret_key="test",
        region="us-east-1",
    )


@mock_aws
def test_ciclo_de_vida_completo():
    storage = _storage()
    ruta = "sire/uploads/1/empresa.csv"

    devuelto = storage.save(ruta, b"contenido")
    assert devuelto == ruta
    assert storage.exists(ruta) is True
    assert storage.read(ruta) == b"contenido"
    assert storage.size(ruta) == len(b"contenido")

    storage.delete(ruta)
    assert storage.exists(ruta) is False


@mock_aws
def test_open_write_streaming():
    storage = _storage()
    ruta = "sire/reports/1/reporte.bin"
    datos = b"x" * 5000

    with storage.open_write(ruta) as f:
        f.write(datos)

    assert storage.read(ruta) == datos


@mock_aws
def test_exists_falso_si_no_existe():
    storage = _storage()
    assert storage.exists("scanner/docs/9/inexistente.pdf") is False


@pytest.mark.parametrize("ruta_invalida", ["../etc/passwd", "a/../../b", "/", ""])
def test_key_segura_rechaza_traversal(ruta_invalida: str):
    with pytest.raises(ValueError):
        _key_segura(ruta_invalida)


def test_key_segura_normaliza_slash_inicial():
    assert _key_segura("/sire/uploads/1/x.csv") == "sire/uploads/1/x.csv"
