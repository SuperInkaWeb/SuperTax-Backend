"""
Smoke tests del motor de cómputo portado (Fase 2b-2).

No ejercitan SUNAT (requiere credenciales reales), pero prueban que el motor de
conciliación y el generador de Excel se ejecutan end-to-end tras el port.
"""
from datetime import datetime, timezone

from src.modules.sire.infrastructure.reconciliation.engine import reconcile
from src.modules.sire.infrastructure.report.excel_generator import generate_excel


def test_reconcile_ejecuta_con_entradas_vacias():
    out = reconcile([], [], "compras", None, sunat_extra=None, periodo="202601")
    assert out.scenario_a == []
    assert out.scenario_b == []
    assert out.igv_diferencia_total == 0.0
    assert out.tiene_alertas_rojas is False


def test_generate_excel_produce_un_xlsx():
    out = reconcile([], [], "compras", None, sunat_extra=None, periodo="202601")
    contenido = generate_excel(
        output=out,
        empresa_nombre="Estudio X",
        ruc="20123456789",
        periodo="202601",
        tipo_libro="compras",
        propuesta_generada=datetime.now(timezone.utc),
        cobertura=None,
        sin_sire=False,
        meses_no_disponibles=None,
    )
    assert isinstance(contenido, (bytes, bytearray))
    assert contenido[:2] == b"PK"  # un .xlsx es internamente un ZIP
