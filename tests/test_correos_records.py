"""Revisión de registros (Fase 5): interpretación de correcciones, serialización y rutas.

Lógica pura, sin BD: la validación de correcciones usa los objetos de valor del
dominio y la serialización construye modelos en memoria (sin sesión).
"""
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from src.modules.correos.api.routes import _nombre_seguro
from src.modules.correos.api.schemas import RecordSummary
from src.modules.correos.application.record_service import CorreccionInvalida, _interpretar
from src.modules.correos.infrastructure.models import ExtractedRecordModel

_RUC = "20131312955"


# ── Interpretación de correcciones ───────────────────────────────────

def test_interpretar_convierte_a_tipos_de_columna():
    valores = _interpretar({
        "ruc_contribuyente": _RUC,
        "periodo": "03/2026",
        "fecha_de_pago": "15/04/2026",
        "importe_pagado": "S/ 75.00",
        "nombre_contribuyente": "  INVERSIONES LIMA SAC  ",
    })
    assert valores["ruc_contribuyente"] == _RUC
    assert valores["periodo"] == "202603"
    assert str(valores["fecha_de_pago"]) == "2026-04-15"
    assert valores["importe_pagado"] == Decimal("75.00")
    assert valores["nombre_contribuyente"] == "INVERSIONES LIMA SAC"


def test_interpretar_rechaza_campo_no_corregible():
    with pytest.raises(CorreccionInvalida) as exc:
        _interpretar({"perfil": "otro"})
    assert exc.value.campo == "perfil"


def test_interpretar_rechaza_valor_invalido():
    with pytest.raises(CorreccionInvalida) as exc:
        _interpretar({"ruc_contribuyente": "12345"})  # RUC inválido
    assert exc.value.campo == "ruc_contribuyente"


def test_interpretar_rechaza_correcciones_vacias():
    with pytest.raises(CorreccionInvalida):
        _interpretar({})


# ── Serialización del registro ───────────────────────────────────────

def _modelo(**extra) -> ExtractedRecordModel:
    base = dict(
        id=1, job_id=7, attachment_id=3, ruc_contribuyente=_RUC,
        nombre_contribuyente="INVERSIONES LIMA SAC", ruc_inquilino=None, nombre_inquilino="",
        tipo_doc_inquilino="", tipo_de_bien="", periodo="202603", fecha_de_pago=None,
        numero_de_operacion="987654321", monto_alquiler=Decimal("1500.00"),
        tributo_resultante=None, importe_pagado=Decimal("75.00"), intereses_moratorios=None,
        moneda="PEN", confianza_por_campo={"ruc_contribuyente": 0.98, "importe_pagado": 0.5},
        completitud="partial", estado_de_revision="pending", estrategia_usada="native_pdf_text",
        created_at=datetime.now(UTC),
    )
    base.update(extra)
    return ExtractedRecordModel(**base)


def test_record_summary_serializa_importes_y_dudosos():
    salida = RecordSummary.from_model(
        _modelo(), {"nombre_adjunto": "recibo.pdf", "remitente": "a@b.com"}
    )
    assert salida.importe_pagado == "75.00"
    assert salida.monto_alquiler == "1500.00"
    assert salida.tributo_resultante is None
    # importe_pagado tiene confianza 0.5 (< umbral) → marcado como dudoso.
    assert salida.campos_dudosos == ["importe_pagado"]
    assert salida.origen.nombre_adjunto == "recibo.pdf"


def test_record_summary_sin_origen():
    salida = RecordSummary.from_model(_modelo())
    assert salida.origen.nombre_adjunto == ""


# ── Saneado de nombre de archivo (anti-inyección de cabecera) ────────

def test_nombre_seguro_limpia_caracteres_peligrosos():
    assert _nombre_seguro('a"b\r\nc.pdf') == "abc.pdf"
    assert _nombre_seguro("") == "documento"
    assert _nombre_seguro("../../etc/passwd") == "....etcpasswd"


# ── Rutas montadas ───────────────────────────────────────────────────

def test_rutas_records_montadas():
    from src.main import create_app

    paths = create_app().openapi()["paths"]
    assert "/api/correos/records" in paths
    assert "/api/correos/records/{record_id}" in paths
    assert "/api/correos/records/{record_id}/document" in paths
    assert "/api/correos/review" in paths
    assert "/api/correos/review/count" in paths
    # PATCH y DELETE comparten el path de GET /records/{record_id}.
    ops = paths["/api/correos/records/{record_id}"]
    assert {"get", "patch", "delete"} <= set(ops)
