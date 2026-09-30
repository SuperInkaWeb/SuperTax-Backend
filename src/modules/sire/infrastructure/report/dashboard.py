"""
Hoja «Dashboard» del reporte de ventas: comparación interactiva de boletas
PLE vs SIRE por serie.

Se arma sobre `boletas_agregadas` (una fila por serie+día ya cruzada). El tablero
es Excel nativo: un desplegable elige la serie y las fórmulas INDEX/MATCH + los
gráficos se actualizan solos al abrirlo. Los datos viven en hojas ocultas para no
ensuciar el libro. Ver la memoria `sire-boletas-dashboard-requerimientos`.
"""
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

_TITULO_FONT = Font(color="FFFFFF", bold=True, size=14)
_TITULO_FILL = PatternFill("solid", fgColor="1F4E78")
_LABEL_FONT = Font(bold=True, color="1F4E78")
_KPI_FONT = Font(bold=True, size=14)
_HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
_HEADER_FONT = Font(color="FFFFFF", bold=True)
_MONEDA = '"S/" #,##0.00'
_NUM = "#,##0.00"

_HOJA_SERIES = "Datos series"
# Matrices serie×día visibles (equivalen a las hojas pivote PLE/SIRE del cliente).
_HOJA_PLE = "PLE por dia"
_HOJA_SIRE = "SIRE por dia"


def _preparar(boletas):
    """Agrupa las boletas para el tablero: totales por serie (ordenadas por
    diferencia absoluta) y matrices serie×día de cada lado."""
    fechas = sorted({b.fecha for b in boletas})
    tot_ple: dict[str, float] = {}
    tot_sire: dict[str, float] = {}
    ple_sf: dict[tuple[str, str], float] = {}
    sire_sf: dict[tuple[str, str], float] = {}
    for b in boletas:
        tot_ple[b.serie] = tot_ple.get(b.serie, 0.0) + b.igv_empresa
        tot_sire[b.serie] = tot_sire.get(b.serie, 0.0) + b.igv_sunat
        ple_sf[(b.serie, b.fecha)] = ple_sf.get((b.serie, b.fecha), 0.0) + b.igv_empresa
        sire_sf[(b.serie, b.fecha)] = sire_sf.get((b.serie, b.fecha), 0.0) + b.igv_sunat
    series = sorted(tot_ple.keys() | tot_sire.keys(),
                    key=lambda s: -abs(tot_ple.get(s, 0.0) - tot_sire.get(s, 0.0)))
    return fechas, series, tot_ple, tot_sire, ple_sf, sire_sf


def _hoja_series(wb, series, tot_ple, tot_sire):
    ws = wb.create_sheet(_HOJA_SERIES)
    ws.append(["Serie", "PLE", "SIRE", "Diferencia", "Dif. absoluta", "% dif"])
    for s in series:
        ple = round(tot_ple.get(s, 0.0), 2)
        sire = round(tot_sire.get(s, 0.0), 2)
        dif = round(ple - sire, 2)
        pct = abs(dif) / ple if ple else 0.0
        ws.append([s, ple, sire, dif, abs(dif), round(pct, 4)])
    ws.sheet_state = "hidden"
    return ws


def _hoja_dia(wb, titulo, series, fechas, valores):
    """Matriz VISIBLE serie×día del IGV (fila = serie, columna = día) + Total, como
    las hojas pivote PLE/SIRE del cliente. La cabecera va en la fila 1 y los datos
    desde la fila 2 (el Dashboard busca por serie con MATCH, sin depender del orden)."""
    ws = wb.create_sheet(titulo)
    ws.append(["Serie", *fechas, "Total"])
    for s in series:
        vals = [round(valores.get((s, f), 0.0), 2) for f in fechas]
        ws.append([s, *vals, round(sum(vals), 2)])

    for celda in ws[1]:
        celda.fill = _HEADER_FILL
        celda.font = _HEADER_FONT
        celda.alignment = Alignment(horizontal="center")
    ws.freeze_panes = "B2"
    ws.column_dimensions["A"].width = 10
    for col in range(2, len(fechas) + 3):          # días + columna Total
        ws.column_dimensions[get_column_letter(col)].width = 11
        for fila in range(2, len(series) + 2):
            ws.cell(row=fila, column=col).number_format = _NUM
    return ws


def agregar_hoja_dashboard(wb, boletas) -> None:
    """Crea la hoja «Dashboard» (y sus hojas de datos ocultas) a partir de las
    boletas ya cruzadas por serie+día. No hace nada si no hay boletas."""
    if not boletas:
        return

    fechas, series, tot_ple, tot_sire, ple_sf, sire_sf = _preparar(boletas)
    _hoja_series(wb, series, tot_ple, tot_sire)
    # Las matrices visibles se ordenan por serie (más fácil de hojear); el
    # Dashboard busca por MATCH, así que el orden no afecta sus fórmulas.
    series_ordenadas = sorted(series)
    _hoja_dia(wb, _HOJA_PLE, series_ordenadas, fechas, ple_sf)
    _hoja_dia(wb, _HOJA_SIRE, series_ordenadas, fechas, sire_sf)

    lr = len(series) + 1          # última fila con datos en «Datos series»
    n_dias = len(fechas)
    ultima_col_dia = get_column_letter(1 + n_dias)
    ws = wb.create_sheet("Dashboard", 0)   # primera pestaña
    ws.sheet_view.showGridLines = False

    ws.merge_cells("A1:H1")
    ws["A1"] = "Dashboard — boletas de ventas · PLE vs SIRE"
    ws["A1"].font = _TITULO_FONT
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    for celda in ws["A1:H1"][0]:
        celda.fill = _TITULO_FILL
    ws.row_dimensions[1].height = 26

    # Selector de serie (por defecto, la de mayor diferencia).
    ws["A3"] = "Seleccionar serie:"
    ws["A3"].font = _LABEL_FONT
    ws["C3"] = series[0]
    ws["C3"].font = Font(bold=True)
    ws["C3"].fill = PatternFill("solid", fgColor="FFF2CC")
    ws["C3"].alignment = Alignment(horizontal="center")
    dv = DataValidation(type="list", formula1=f"'{_HOJA_SERIES}'!$A$2:$A${lr}", allow_blank=False)
    ws.add_data_validation(dv)
    dv.add(ws["C3"])
    ws["E3"] = "(lista ordenada de mayor a menor diferencia)"
    ws["E3"].font = Font(italic=True, size=9, color="595959")

    # KPIs de la serie elegida (INDEX/MATCH sobre «Datos series»).
    def _busca(col_letra):
        return (f"=IFERROR(INDEX('{_HOJA_SERIES}'!${col_letra}$2:${col_letra}${lr},"
                f"MATCH($C$3,'{_HOJA_SERIES}'!$A$2:$A${lr},0)),0)")

    kpis = [("A", "Total PLE", _busca("B"), _MONEDA),
            ("C", "Total SIRE", _busca("C"), _MONEDA),
            ("E", "Diferencia (PLE-SIRE)", "=A6-C6", _MONEDA),
            ("G", "% dif sobre PLE", "=IFERROR((A6-C6)/A6,0)", "0.0%")]
    for col, etiqueta, formula, fmt in kpis:
        ws[f"{col}5"] = etiqueta
        ws[f"{col}5"].font = Font(size=10, color="595959")
        celda = ws[f"{col}6"]
        celda.value = formula
        celda.font = _KPI_FONT
        celda.number_format = fmt

    # Detalle diario de la serie elegida (alimenta el gráfico de barras).
    ws["A8"] = "PLE vs SIRE por día — serie seleccionada"
    ws["A8"].font = _LABEL_FONT
    ws["A9"] = "Fecha"
    ws["A10"] = "PLE"
    ws["A11"] = "SIRE"
    for j, fecha in enumerate(fechas):
        col = get_column_letter(2 + j)
        ws[f"{col}9"] = fecha
        ws[f"{col}9"].font = Font(size=9)
        for fila, hoja in ((10, _HOJA_PLE), (11, _HOJA_SIRE)):
            ws[f"{col}{fila}"] = (
                f"=IFERROR(INDEX('{hoja}'!$B$2:${ultima_col_dia}${lr},"
                f"MATCH($C$3,'{hoja}'!$A$2:$A${lr},0),{j + 1}),0)"
            )
            ws[f"{col}{fila}"].number_format = _MONEDA

    grafico_dia = BarChart()
    grafico_dia.title = "PLE vs SIRE por día"
    grafico_dia.type = "col"
    grafico_dia.height = 7
    grafico_dia.width = 20
    datos = Reference(ws, min_col=1, min_row=10, max_col=1 + n_dias, max_row=11)
    categorias = Reference(ws, min_col=2, min_row=9, max_col=1 + n_dias, max_row=9)
    grafico_dia.add_data(datos, titles_from_data=True, from_rows=True)
    grafico_dia.set_categories(categorias)
    ws.add_chart(grafico_dia, "A13")

    # Top series por diferencia (sobre «Datos series», ya ordenada).
    top = min(10, len(series))
    grafico_top = BarChart()
    grafico_top.title = "Top series con mayor diferencia"
    grafico_top.type = "bar"
    grafico_top.height = 7
    grafico_top.width = 20
    ser = wb[_HOJA_SERIES]
    grafico_top.add_data(Reference(ser, min_col=5, min_row=1, max_row=1 + top), titles_from_data=True)
    grafico_top.set_categories(Reference(ser, min_col=1, min_row=2, max_row=1 + top))
    grafico_top.legend = None
    ws.add_chart(grafico_top, "A28")

    ws.column_dimensions["A"].width = 20
    for col in "BCDEFGH":
        ws.column_dimensions[col].width = 14
