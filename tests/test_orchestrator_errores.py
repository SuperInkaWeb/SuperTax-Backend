"""Tests del clasificador de errores de la conciliación: cada fallo da un mensaje
específico y accionable, en vez de un genérico 'error inesperado'."""
import httpx

from src.modules.sire.infrastructure.reconciliation.orchestrator import _mensaje_error


def _http_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", "https://api-sire.sunat.gob.pe/x")
    resp = httpx.Response(code, request=req)
    return httpx.HTTPStatusError("err", request=req, response=resp)


def test_401_habla_de_credenciales():
    msg = _mensaje_error(_http_error(401))
    assert "401" in msg and "credenciales" in msg.lower()


def test_429_habla_de_limite():
    assert "429" in _mensaje_error(_http_error(429))


def test_403_bloqueo():
    assert "403" in _mensaje_error(_http_error(403))


def test_transport_error_habla_de_conexion():
    assert "conectar" in _mensaje_error(httpx.ConnectError("x")).lower()


def test_valueerror_se_respeta():
    assert _mensaje_error(ValueError("mapeo inválido")) == "mapeo inválido"


def test_error_desconocido_es_generico():
    assert "inesperado" in _mensaje_error(RuntimeError("boom")).lower()
