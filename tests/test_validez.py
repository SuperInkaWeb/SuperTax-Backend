"""
Tests del cliente de validez de comprobante (req 5), con httpx mockeado (sin red):
mapeo de estadoCp a etiqueta, y degradación a None cuando SUNAT no responde 200.
"""
import httpx
import pytest

from src.modules.sire.infrastructure.sunat import validez


class _Resp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


def test_obtener_token_ok(monkeypatch):
    monkeypatch.setattr(
        validez.httpx, "post", lambda *a, **k: _Resp(200, {"access_token": "tok123"})
    )
    assert validez.obtener_token("cid", "secret") == "tok123"


def test_obtener_token_scope_rechazado_devuelve_none(monkeypatch):
    monkeypatch.setattr(validez.httpx, "post", lambda *a, **k: _Resp(403, {}))
    assert validez.obtener_token("cid", "secret") is None


def test_obtener_token_error_red_devuelve_none(monkeypatch):
    def _boom(*a, **k):
        raise httpx.ConnectError("sin red")

    monkeypatch.setattr(validez.httpx, "post", _boom)
    assert validez.obtener_token("cid", "secret") is None


@pytest.mark.parametrize(
    ("estado", "etiqueta"),
    [("1", "ACEPTADO"), ("0", "NO EXISTE"), ("2", "ANULADO")],
)
def test_validar_mapea_estado_cp(monkeypatch, estado, etiqueta):
    monkeypatch.setattr(
        validez.httpx, "post", lambda *a, **k: _Resp(200, {"data": {"estadoCp": estado}})
    )
    rpta = validez.validar(
        "tok", "20563529378", "20563529378", "01", "F001", "00000010", "07/09/2026", monto=66.0
    )
    assert rpta == etiqueta


def test_validar_no_200_devuelve_none(monkeypatch):
    monkeypatch.setattr(validez.httpx, "post", lambda *a, **k: _Resp(422, {}))
    assert validez.validar(
        "tok", "20563529378", "20563529378", "01", "F001", "00000010", "07/09/2026"
    ) is None


def test_validar_numero_invalido_devuelve_none():
    assert validez.validar(
        "tok", "20563529378", "20563529378", "01", "F001", "   ", "07/09/2026"
    ) is None
