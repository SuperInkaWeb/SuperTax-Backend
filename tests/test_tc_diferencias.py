"""
Tests de la comparación de tipo de cambio (req 2.2): detección de diferencias del
PLE vs TC oficial, excepción NC/ND (fecha del doc modificado), cálculo de Dif IGV
y la hoja "Diferencias TC" del Excel. Valores alineados con el caso real de Carmen.
"""
from datetime import date

from openpyxl import Workbook

from src.modules.sire.infrastructure.reconciliation.tc_diferencias import (
    DiferenciaTC,
    comparar_tipo_cambio,
)
from src.modules.sire.infrastructure.report.excel_generator import (
    _agregar_hoja_diferencias_tc,
)


def _linea_ple(
    *, tipo="01", fecha_emi="07/09/2026", serie="F000", numero="00096583",
    razon="MI FARMA S.A.C", igv="2338.12", total="15327.58", moneda="USD",
    tc="3.418", fecha_doc_mod=" ", cuo="43083525", periodo="20260900",
) -> str:
    campos = [" "] * 36
    campos[0] = periodo
    campos[1] = cuo
    campos[3] = fecha_emi
    campos[5] = tipo
    campos[6] = serie
    campos[7] = numero
    campos[11] = razon
    campos[15] = igv
    campos[24] = total
    campos[25] = moneda
    campos[26] = tc
    campos[27] = fecha_doc_mod
    return "|".join(campos)


def _contenido(*lineas: str) -> bytes:
    return "\n".join(lineas).encode("latin-1")


def test_detecta_diferencia_y_calcula_dif_igv():
    """Factura USD 07/09: TC PLE 3.418 vs oficial 3.369 → Dif IGV 33.52 (caso Carmen)."""
    contenido = _contenido(_linea_ple(tc="3.418", igv="2338.12"))
    difs = comparar_tipo_cambio(contenido, "ventas", {"2026-09-07": "3.369"})
    assert len(difs) == 1
    d = difs[0]
    assert d.tc_ple == 3.418
    assert d.tc_sunat == 3.369
    assert d.dif_igv == 33.52  # 2338.12 * (3.418-3.369)/3.418 redondeado
    assert d.num_documento == "F000-00096583"
    assert d.tipo_label == "01 - Factura"


def test_ignora_soles():
    contenido = _contenido(_linea_ple(moneda="PEN", tc="1.000"))
    assert comparar_tipo_cambio(contenido, "ventas", {"2026-09-07": "3.369"}) == []


def test_ignora_cuando_el_tc_calza_exacto():
    contenido = _contenido(_linea_ple(tc="3.369"))
    assert comparar_tipo_cambio(contenido, "ventas", {"2026-09-07": "3.369"}) == []


def test_nc_usa_el_tc_de_la_fecha_del_doc_modificado():
    """Una NC (07) en USD compara contra el TC de la fecha del doc modificado, no
    el de su propia emisión."""
    contenido = _contenido(
        _linea_ple(tipo="07", fecha_emi="15/09/2026", fecha_doc_mod="04/09/2026", tc="3.372")
    )
    # Si usara la emisión (15/09) calzaría (3.372). Debe usar el doc modificado (04/09=3.369).
    difs = comparar_tipo_cambio(
        contenido, "ventas", {"2026-09-15": "3.372", "2026-09-04": "3.369"}
    )
    assert len(difs) == 1
    assert difs[0].tc_sunat == 3.369
    assert difs[0].fecha_doc_mod == date(2026, 9, 4)


def test_solo_ventas():
    contenido = _contenido(_linea_ple())
    assert comparar_tipo_cambio(contenido, "compras", {"2026-09-07": "3.369"}) == []


def test_omite_si_no_hay_tc_oficial_para_la_fecha():
    contenido = _contenido(_linea_ple(fecha_emi="07/09/2026"))
    assert comparar_tipo_cambio(contenido, "ventas", {"2026-09-04": "3.369"}) == []


def test_hoja_diferencias_tc_se_arma_con_totales():
    difs = [
        DiferenciaTC(
            cuo="1", periodo="20260900", fecha_emi=date(2026, 9, 7), fecha_doc_mod=None,
            tipo_label="01 - Factura", num_documento="F000-1", razon_social="ACME",
            v_igv=100.0, v_total=600.0, tc_ple=3.418, tc_sunat=3.369, dif_igv=1.43,
        ),
        DiferenciaTC(
            cuo="2", periodo="20260900", fecha_emi=date(2026, 9, 8), fecha_doc_mod=None,
            tipo_label="01 - Factura", num_documento="F000-2", razon_social="BETA",
            v_igv=200.0, v_total=900.0, tc_ple=3.418, tc_sunat=3.372, dif_igv=2.69,
        ),
    ]
    wb = Workbook()
    _agregar_hoja_diferencias_tc(wb, difs)
    ws = wb["Diferencias TC"]
    assert ws.cell(row=1, column=1).value == "#"
    assert ws.cell(row=2, column=11).value == 3.418  # TC PLE
    assert ws.cell(row=2, column=12).value == 3.369  # TC SUNAT
    # Fila de totales: Dif IGV = 1.43 + 2.69 = 4.12
    fila_total = len(difs) + 2
    assert ws.cell(row=fila_total, column=8).value == "TOTAL"
    assert ws.cell(row=fila_total, column=13).value == 4.12
