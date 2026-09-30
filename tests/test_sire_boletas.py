"""
Cruce de boletas por serie + día (Fase 1).

Verifica la regla de normalización de serie, que cualquier estilo de agrupación
(sueltas, por rangos, resumen) colapse al mismo total diario, la clasificación
del cruce, y que el motor aparte las boletas de los escenarios A/B/C/D en ventas.
"""
from datetime import datetime, timezone
from io import BytesIO

from openpyxl import load_workbook

from src.modules.sire.infrastructure.parser.empresa_file import EmpresaRecord
from src.modules.sire.infrastructure.parser.sunat_propuesta import SunatRecord
from src.modules.sire.infrastructure.reconciliation.boletas import (
    BOLETA_CUADRA,
    BOLETA_DIFIERE,
    BOLETA_SOLO_EMPRESA,
    BOLETA_SOLO_SUNAT,
    comparar_boletas,
    es_boleta,
    normalizar_serie,
)
from src.modules.sire.infrastructure.reconciliation.engine import reconcile
from src.modules.sire.infrastructure.reconciliation.observaciones import (
    detectar_correlatividad,
    detectar_duplicados,
)
from src.modules.sire.infrastructure.report.excel_generator import generate_excel


def _emp(serie, igv, base=0.0, importe=0.0, fecha="2026-05-01", tipo="03",
         numero="1", numero_final=""):
    return EmpresaRecord(
        tipo_cdp=tipo, serie=serie, numero=numero, numero_final=numero_final,
        importe_total=importe, fecha_emision=fecha, base_imponible=base, igv=igv,
    )


def _sun(serie, igv, base=0.0, importe=0.0, fecha="2026-05-01", tipo="03", numero="1"):
    return SunatRecord(
        tipo_cdp=tipo, serie=serie, numero=numero, fecha_emision=fecha,
        base_imponible=base, igv=igv, importe_total=importe, tipo_cambio=1.0,
    )


# ── Normalización de serie ──────────────────────────────────────────────

def test_normalizar_serie_digitos_a_b_mas_tres():
    assert normalizar_serie("0127") == "B127"
    assert normalizar_serie("0006") == "B006"
    assert normalizar_serie("58") == "B058"


def test_normalizar_serie_con_letra_se_respeta():
    assert normalizar_serie("B621") == "B621"
    assert normalizar_serie(" b001 ") == "B001"


def test_normalizar_serie_no_fusiona_series_distintas():
    # El caso Tambo: series con letras propias no deben colapsar a la misma clave.
    claves = {normalizar_serie(s) for s in ("B621", "B62I", "BM49", "B007")}
    assert len(claves) == 4


def test_es_boleta():
    assert es_boleta(_emp("B001", 10)) is True
    assert es_boleta(_emp("F001", 10, tipo="01")) is False


# ── La agregación es independiente del estilo ───────────────────────────

def test_estilos_de_agrupacion_dan_el_mismo_total():
    sunat = [_sun("B001", 10, base=55.0, numero=str(n)) for n in range(1, 4)]  # 3 sueltas

    sueltas = [_emp("B001", 10, base=55.0, numero=str(n)) for n in range(1, 4)]
    rango = [_emp("B001", 30, base=165.0)]        # una fila que agrupa las 3
    resumen = [_emp("B001", 30, base=165.0)]      # "CLIENTES VARIOS"

    for estilo in (sueltas, rango, resumen):
        res = comparar_boletas(estilo, sunat)
        assert len(res) == 1
        assert res[0].estado == BOLETA_CUADRA
        assert res[0].igv_empresa == 30.0
        assert res[0].igv_sunat == 30.0


# ── Clasificación del cruce ─────────────────────────────────────────────

def test_serie_normalizada_hace_casar_ambos_lados():
    # Tu archivo escribe '0127', SUNAT 'B127': deben ser la misma serie.
    res = comparar_boletas([_emp("0127", 100, base=555.0)], [_sun("B127", 100, base=555.0)])
    assert len(res) == 1
    assert res[0].serie == "B127"
    assert res[0].estado == BOLETA_CUADRA


def test_diferencia_marca_campos_y_alerta_roja():
    res = comparar_boletas([_emp("B001", 100)], [_sun("B001", 80)])
    assert res[0].estado == BOLETA_DIFIERE
    assert "igv" in res[0].campos_diferentes
    assert res[0].es_alerta_roja is True
    assert res[0].diferencia_igv == 20.0


def test_solo_en_un_lado():
    solo_emp = comparar_boletas([_emp("B001", 50)], [])
    assert solo_emp[0].estado == BOLETA_SOLO_EMPRESA
    solo_sun = comparar_boletas([], [_sun("B001", 50)])
    assert solo_sun[0].estado == BOLETA_SOLO_SUNAT


# ── Integración con el motor ────────────────────────────────────────────

def test_reconcile_boletas_van_a_abcd_y_al_detalle():
    empresa = [
        _emp("F001", 18, base=100.0, importe=118.0, tipo="01"),   # factura
        _emp("0127", 30, base=165.0),                              # boleta agrupada
    ]
    sunat = [
        _sun("F001", 18, base=100.0, importe=118.0, tipo="01"),   # misma factura
        _sun("B127", 30, base=165.0, numero="9"),                  # boleta suelta
    ]
    out = reconcile(empresa, sunat, "ventas", None, periodo="202605")

    # Detalle serie+día conservado aparte (para hoja Cruce boletas y dashboard).
    assert len(out.boletas_agregadas) == 1
    assert out.boletas_agregadas[0].estado == BOLETA_CUADRA

    # La boleta aparece también en D como fila agregada, con la serie normalizada.
    boletas_en_d = [r for r in out.scenario_d if r.tipo_cdp == "03"]
    assert len(boletas_en_d) == 1
    assert boletas_en_d[0].serie == "B127"
    assert any(r.tipo_cdp == "01" for r in out.scenario_d)   # la factura también cuadra
    assert out.scenario_a == [] and out.scenario_b == []


def test_reconcile_boleta_con_diferencia_va_a_escenario_c():
    out = reconcile([_emp("B001", 100, base=500.0)],
                    [_sun("B001", 80, base=500.0)], "ventas", None, periodo="202605")
    boletas_c = [r for r in out.scenario_c if r.tipo_cdp == "03"]
    assert len(boletas_c) == 1
    assert "igv" in boletas_c[0].campos_diferentes


def test_reconcile_compras_no_agrega_boletas():
    out = reconcile([_emp("B001", 10)], [_sun("B001", 10)], "compras", None, periodo="202605")
    assert out.boletas_agregadas == []


def test_generate_excel_incluye_hoja_cruce_boletas():
    out = reconcile(
        [_emp("0127", 30, base=165.0, importe=195.0)],
        [_sun("B127", 30, base=165.0, importe=195.0, numero="9")],
        "ventas", None, periodo="202605",
    )
    xlsx = generate_excel(
        output=out, empresa_nombre="ARUMA", ruc="20600657888",
        periodo="202605", tipo_libro="ventas",
        propuesta_generada=datetime.now(timezone.utc),
    )
    wb = load_workbook(BytesIO(xlsx))
    assert "Cruce boletas" in wb.sheetnames
    # La boleta cuadra → aparece en D como fila tipo 03.
    filas_d = wb["D - Coinciden OK"].iter_rows(min_row=2, values_only=True)
    assert any(fila[0] == "03" for fila in filas_d)


def test_dashboard_se_genera_con_desplegable_y_graficos():
    out = reconcile(
        [_emp("0001", 300, base=1666.67, importe=1966.67, fecha="2026-08-01"),
         _emp("B057", 90, base=500.0, importe=590.0, fecha="2026-08-01")],
        [_sun("B001", 300, base=1666.67, importe=1966.67, fecha="2026-08-01", numero="1"),
         _sun("B120", 120, base=666.67, importe=786.67, fecha="2026-08-01", numero="9")],
        "ventas", None, periodo="202608",
    )
    xlsx = generate_excel(
        output=out, empresa_nombre="ARUMA", ruc="20600657888",
        periodo="202608", tipo_libro="ventas",
        propuesta_generada=datetime.now(timezone.utc),
    )
    wb = load_workbook(BytesIO(xlsx))
    assert wb.sheetnames[0] == "Dashboard"                       # primera pestaña
    ws = wb["Dashboard"]
    assert len(ws.data_validations.dataValidation) == 1          # desplegable de serie
    assert len(ws._charts) == 2                                  # gráfico por día + top series
    assert wb["Datos series"].sheet_state == "hidden"            # ranking/dropdown: apoyo oculto
    assert wb["PLE por dia"].sheet_state == "visible"            # matriz serie×día visible
    assert wb["SIRE por dia"].sheet_state == "visible"
    assert "Diferencias por dia" in wb.sheetnames                # matriz de diferencias
    assert any(                                                  # guía de hojas en Resumen
        c == "GUÍA DE HOJAS"
        for fila in wb["Resumen"].iter_rows(values_only=True) for c in fila
    )


# ── Observaciones: correlatividad y duplicados ──────────────────────────

def test_correlatividad_detecta_huecos_en_nc():
    recs = [_emp("BC01", -10, tipo="07", numero=str(n)) for n in (1, 2, 5)]  # faltan 3, 4
    res = detectar_correlatividad(recs)
    assert len(res) == 1
    assert res[0].tipo == "07"
    assert res[0].faltantes == "3-4"
    assert res[0].cantidad == 2


def test_correlatividad_ignora_comprobantes_no_nc():
    recs = [_emp("F001", 10, tipo="01", numero=str(n)) for n in (1, 5)]  # facturas
    assert detectar_correlatividad(recs) == []


def test_correlatividad_boletas_por_rangos():
    recs = [
        _emp("B001", 10, numero="100", numero_final="150"),
        _emp("B001", 10, numero="151", numero_final="200"),
        _emp("B001", 10, numero="205", numero_final="260"),   # salto: faltan 201-204
    ]
    res = detectar_correlatividad(recs)
    assert len(res) == 1
    assert res[0].tipo == "03"
    assert res[0].faltantes == "201-204"
    assert res[0].cantidad == 4


def test_correlatividad_numeracion_irregular_no_lista_millones():
    recs = [
        _emp("0070", 10, numero="7000141", numero_final="7000200"),
        _emp("0070", 10, numero="70000116", numero_final="70000140"),   # 7 vs 8 dígitos
    ]
    res = detectar_correlatividad(recs)
    assert len(res) == 1
    assert "irregular" in res[0].faltantes
    assert res[0].cantidad > 500


def test_duplicados_detecta_clave_repetida():
    recs = [
        _emp("BC01", -10, tipo="07", numero="100"),
        _emp("BC01", -10, tipo="07", numero="100"),
        _emp("BC01", -10, tipo="07", numero="101"),
    ]
    dups = detectar_duplicados(recs)
    assert len(dups) == 1
    assert dups[0].numero == "100"
    assert dups[0].veces == 2


def test_reporte_incluye_hoja_observaciones():
    empresa = [
        _emp("BC01", -10, base=-55.0, importe=-65.0, tipo="07", numero="1"),
        _emp("BC01", -10, base=-55.0, importe=-65.0, tipo="07", numero="3"),  # falta 2
        _emp("F001", 18, base=100.0, importe=118.0, tipo="01", numero="1"),
        _emp("F001", 18, base=100.0, importe=118.0, tipo="01", numero="1"),   # duplicado
    ]
    out = reconcile(empresa, [], "ventas", None, periodo="202608")
    assert any(o.tipo == "07" for o in out.correlatividad)
    assert any(d.veces == 2 for d in out.duplicados)
    xlsx = generate_excel(
        output=out, empresa_nombre="X", ruc="20600657888",
        periodo="202608", tipo_libro="ventas",
        propuesta_generada=datetime.now(timezone.utc),
    )
    assert "Observaciones" in load_workbook(BytesIO(xlsx)).sheetnames
