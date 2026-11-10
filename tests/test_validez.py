"""
Tests del cliente de validez de comprobante (req 5), con httpx mockeado (sin red):
mapeo de estadoCp a etiqueta, y degradación a None cuando SUNAT no responde 200.
"""
import httpx
import pytest

from src.modules.sire.infrastructure.reconciliation import worker
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


# ─────────── Resguardos del worker (req 5.3) ───────────
def test_fecha_ddmmyyyy_normaliza():
    assert worker._fecha_ddmmyyyy("07/09/2026") == "07/09/2026"
    assert worker._fecha_ddmmyyyy("2026-09-07") == "07/09/2026"
    assert worker._fecha_ddmmyyyy("") == ""


def test_consultar_validez_refresca_token_al_expirar(monkeypatch):
    tokens = iter(["t1", "t2"])
    monkeypatch.setattr(worker.validez_sunat, "obtener_token", lambda *a, **k: next(tokens))

    def _validar(token, *a, **k):
        if token == "t1":
            raise worker.validez_sunat.TokenExpirado()
        return "ACEPTADO"

    monkeypatch.setattr(worker.validez_sunat, "validar", _validar)
    items = [(("01", "F001", "00000010"), "01", "F001", "00000010", "07/09/2026", 66.0)]
    out = worker._consultar_validez("cid", "sec", "20563529378", items)
    assert out == {("01", "F001", "00000010"): "ACEPTADO"}


def test_consultar_validez_sin_token_devuelve_vacio(monkeypatch):
    monkeypatch.setattr(worker.validez_sunat, "obtener_token", lambda *a, **k: None)
    items = [(("01", "F", "1"), "01", "F", "1", "07/09/2026", 1.0)]
    assert worker._consultar_validez("c", "s", "r", items) == {}
