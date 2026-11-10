"""
Tests de duplicidad entre periodos y correlatividad vs mes anterior (reqs 3 y 4).
Criterio validado contra el caso real de Tambo: la duplicidad cruzada excluye
boletas (03) y las claves ya duplicadas dentro del mes.
"""
from openpyxl import Workbook

from src.modules.sire.infrastructure.reconciliation import cruce_periodos as cp
from src.modules.sire.infrastructure.reconciliation.ple_ventas import parse_ple_ventas
from src.modules.sire.infrastructure.report.excel_generator import (
    _agregar_hoja_correlatividad_anterior,
    _agregar_hoja_duplicidad,
)


def _linea(*, tipo="01", serie="F001", numero="00000010", numero_final=" ", periodo="20260900"):
    campos = [" "] * 36
    campos[0] = periodo
    campos[1] = "1"
    campos[3] = "07/09/2026"
    campos[5] = tipo
    campos[6] = serie
    campos[7] = numero
    campos[8] = numero_final
    campos[11] = "CLIENTE"
    campos[15] = "10.00"
    campos[24] = "66.00"
    campos[25] = "PEN"
    campos[26] = "1.000"
    return "|".join(campos)


def _contenido(*lineas: str) -> bytes:
    return "\n".join(lineas).encode("latin-1")


def test_parse_extrae_campos_clave():
    filas = parse_ple_ventas(_contenido(_linea(serie="F001", numero="00000010")))
    assert len(filas) == 1
    assert filas[0].serie == "F001" and filas[0].numero == "00000010"
    assert filas[0].tipo == "01"


def test_duplicados_en_periodo_devuelve_todas_las_ocurrencias():
    filas = parse_ple_ventas(_contenido(
        _linea(numero="00000010"),
        _linea(numero="00000010"),  # repetido
        _linea(numero="00000011"),
    ))
    dups = cp.duplicados_en_periodo(filas)
    assert len(dups) == 2
    assert all(d.numero == "00000010" for d in dups)


def test_duplicados_con_anterior_excluye_boletas_y_ya_duplicados():
    anterior = parse_ple_ventas(_contenido(
        _linea(tipo="01", serie="F001", numero="00000005"),
        _linea(tipo="03", serie="B001", numero="00000007"),
        _linea(tipo="01", serie="F009", numero="00000099"),
    ))
    actual = parse_ple_ventas(_contenido(
        _linea(tipo="01", serie="F001", numero="00000005"),   # colisiona con ago → SÍ
        _linea(tipo="03", serie="B001", numero="00000007"),   # boleta → excluida
        # colisiona con ago PERO está duplicada en el mes → va a su hoja, no aquí
        _linea(tipo="01", serie="F009", numero="00000099"),
        _linea(tipo="01", serie="F009", numero="00000099"),
    ))
    en_periodo = cp.claves_duplicadas_en_periodo(actual)
    dups = cp.duplicados_con_anterior(actual, cp.indice_claves(anterior), en_periodo)
    assert [(d.tipo, d.serie, d.numero) for d in dups] == [("01", "F001", "00000005")]


def test_correlatividad_hueco_pequeno():
    anterior = parse_ple_ventas(_contenido(_linea(tipo="07", serie="NC01", numero="00000010")))
    actual = parse_ple_ventas(_contenido(_linea(tipo="07", serie="NC01", numero="00000012")))
    cortes = cp.cortes_correlatividad(actual, anterior)
    assert len(cortes) == 1
    assert cortes[0].faltantes == "11" and cortes[0].cantidad == 1


def test_correlatividad_sin_hueco_no_reporta():
    anterior = parse_ple_ventas(_contenido(_linea(tipo="07", serie="NC01", numero="00000010")))
    actual = parse_ple_ventas(_contenido(_linea(tipo="07", serie="NC01", numero="00000011")))
    assert cp.cortes_correlatividad(actual, anterior) == []


def test_correlatividad_hueco_enorme_es_irregular():
    anterior = parse_ple_ventas(_contenido(_linea(tipo="03", serie="B001", numero="00000002")))
    actual = parse_ple_ventas(_contenido(_linea(tipo="03", serie="B001", numero="00001352")))
    cortes = cp.cortes_correlatividad(actual, anterior)
    assert len(cortes) == 1
    assert "irregular" in cortes[0].faltantes


def test_hojas_excel_de_duplicidad_y_correlatividad_se_arman():
    filas = parse_ple_ventas(_contenido(
        _linea(tipo="01", serie="F001", numero="00000010"),
        _linea(tipo="01", serie="F001", numero="00000010"),
    ))
    anterior = parse_ple_ventas(_contenido(_linea(tipo="07", serie="NC01", numero="00000010")))
    actual = parse_ple_ventas(_contenido(_linea(tipo="07", serie="NC01", numero="00000012")))

    wb = Workbook()
    _agregar_hoja_duplicidad(wb, "Duplicidad en el periodo", cp.duplicados_en_periodo(filas))
    _agregar_hoja_correlatividad_anterior(wb, cp.cortes_correlatividad(actual, anterior))

    dup = wb["Duplicidad en el periodo"]
    assert dup.cell(row=1, column=6).value == "Num Documento"
    assert dup.cell(row=2, column=6).value == "F001-00000010"
    corr = wb["Correlatividad mes anterior"]
    assert corr.cell(row=2, column=5).value == "11"  # número faltante


def test_hoja_duplicidad_agrega_columna_rpta_sunat():
    filas = parse_ple_ventas(_contenido(
        _linea(tipo="01", serie="F001", numero="00000010"),
        _linea(tipo="01", serie="F001", numero="00000010"),
    ))
    rpta = {("01", "F001", "00000010"): "ACEPTADO"}
    wb = Workbook()
    _agregar_hoja_duplicidad(wb, "Duplicidad en el periodo", filas, rpta)
    ws = wb["Duplicidad en el periodo"]
    assert ws.cell(row=1, column=12).value == "Rpta SUNAT"
    assert ws.cell(row=2, column=12).value == "ACEPTADO"
