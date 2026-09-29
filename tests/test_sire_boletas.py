"""
Cruce de boletas por serie + día (Fase 1).

Verifica la regla de normalización de serie, que cualquier estilo de agrupación
(sueltas, por rangos, resumen) colapse al mismo total diario, la clasificación
del cruce, y que el motor aparte las boletas de los escenarios A/B/C/D en ventas.
"""
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


def _emp(serie, igv, base=0.0, importe=0.0, fecha="2026-05-01", tipo="03", numero="1"):
    return EmpresaRecord(
        tipo_cdp=tipo, serie=serie, numero=numero, importe_total=importe,
        fecha_emision=fecha, base_imponible=base, igv=igv,
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

def test_reconcile_aparta_boletas_de_abcd_en_ventas():
    empresa = [
        _emp("F001", 18, base=100.0, importe=118.0, tipo="01"),   # factura
        _emp("0127", 30, base=165.0),                              # boleta agrupada
    ]
    sunat = [
        _sun("F001", 18, base=100.0, importe=118.0, tipo="01"),   # misma factura
        _sun("B127", 30, base=165.0, numero="9"),                  # boleta suelta
    ]
    out = reconcile(empresa, sunat, "ventas", None, periodo="202605")

    todos = out.scenario_a + out.scenario_b + out.scenario_c + out.scenario_d
    assert all(r.tipo_cdp != "03" for r in todos)          # ninguna boleta en A/B/C/D
    assert len(out.scenario_d) == 1                         # la factura cuadra
    assert len(out.boletas_agregadas) == 1                  # la boleta va aparte
    assert out.boletas_agregadas[0].estado == BOLETA_CUADRA


def test_reconcile_compras_no_agrega_boletas():
    out = reconcile([_emp("B001", 10)], [_sun("B001", 10)], "compras", None, periodo="202605")
    assert out.boletas_agregadas == []
