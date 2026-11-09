"""
Smoke tests del motor de cómputo portado (Fase 2b-2).

No ejercitan SUNAT (requiere credenciales reales), pero prueban que el motor de
conciliación y el generador de Excel se ejecutan end-to-end tras el port.
"""
from datetime import datetime, timezone

from src.modules.sire.infrastructure.parser.empresa_file import parse_empresa_file
from src.modules.sire.infrastructure.reconciliation.engine import reconcile
from src.modules.sire.infrastructure.report.excel_generator import generate_excel


def _fila_ple_ventas(numero: str, razon: str) -> str:
    """Una fila de PLE 14.1 (registro de ventas) con aritmética válida (BI+IGV=total)."""
    campos = [
        "20260800", "1", "M1", "04/08/2026", "01/01/0001", "01", "F001", numero,
        " ", "6", "20111111111", razon,
        "0.00", "100.00", "0.00", "18.00", "0.00", "0.00", "0.00", "0.00", "", "",
        "0.00", "0.00", "118.00", "PEN", "1.000", " ", "1", "",
    ]
    return "|".join(campos)


def test_ple_ventas_no_pierde_comprobante_con_comilla_en_razon_social():
    """Regresión: una comilla (") en la razón social rompía el parseo de pandas y
    descartaba el comprobante (quedaba 'solo en SUNAT'). Caso real F152 de Aruma."""
    txt = (
        _fila_ple_ventas("00000100", "CLIENTE NORMAL SAC") + "\n"
        + _fila_ple_ventas("00000101", '"INGENIERIA Y CONSTRUCCIONES EIRL') + "\n"
    )
    records, mapping = parse_empresa_file(txt.encode("latin-1"))
    assert mapping.known_format
    numeros = {r.key[3] for r in records if r.serie == "F001"}
    assert numeros == {"100", "101"}  # ambos, incluido el de la razón con comilla


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
