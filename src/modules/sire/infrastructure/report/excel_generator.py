import io
import csv
from datetime import datetime
from openpyxl import Workbook
from openpyxl.styles import PatternFill, Font, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from src.modules.sire.infrastructure.reconciliation.engine import ReconciliationOutput, IGV_DIFF_THRESHOLD, TIPO_LABELS


RED_FILL     = PatternFill("solid", fgColor="FFC7CE")
RED_FONT     = Font(color="9C0006")
AMBER_FILL   = PatternFill("solid", fgColor="FFEB9C")
AMBER_FONT   = Font(color="9C6500")
GREEN_FILL   = PatternFill("solid", fgColor="C6EFCE")
GREEN_FONT   = Font(color="006100")
DIFF_FILL    = PatternFill("solid", fgColor="FFD966")
HEADER_FILL  = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT  = Font(color="FFFFFF", bold=True, size=11)
TITLE_FILL   = PatternFill("solid", fgColor="1F4E78")
TITLE_FONT   = Font(color="FFFFFF", bold=True, size=14)
SECTION_FONT = Font(bold=True, size=11, color="1F4E78")
BOLD         = Font(bold=True)
GRAY_FILL    = PatternFill("solid", fgColor="F2F2F2")
NOTE_FONT    = Font(italic=True, size=9, color="595959")

THIN_BORDER = Border(
    left=Side(style="thin", color="BFBFBF"), right=Side(style="thin", color="BFBFBF"),
    top=Side(style="thin", color="BFBFBF"), bottom=Side(style="thin", color="BFBFBF"),
)

NUM_FMT = "#,##0.00"


def _fmt_periodo(p: str) -> str:
    """'202605' → '2026-05'. Vacío se queda vacío."""
    return f"{p[:4]}-{p[4:]}" if p and len(p) == 6 else (p or "")

EXCEL_FORMAT_LIMIT  = 50_000
EXCEL_MAX_DATA_ROWS = 1_048_575


def _set_header_row(ws, headers: list[str], row: int = 1):
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=row, column=col, value=header)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = THIN_BORDER
    ws.row_dimensions[row].height = 28


def _finish_sheet(ws, n_cols: int, num_cols: list[int] | None = None, widths: dict[int, int] | None = None):
    """Autofiltro + panel congelado + formato numérico + anchos."""
    last_col = get_column_letter(n_cols)
    if ws.max_row > 1:
        ws.auto_filter.ref = f"A1:{last_col}{ws.max_row}"
    ws.freeze_panes = "A2"

    if num_cols:
        for col_idx in num_cols:
            for row in range(2, ws.max_row + 1):
                cell = ws.cell(row=row, column=col_idx)
                if isinstance(cell.value, (int, float)):
                    cell.number_format = NUM_FMT

    if widths:
        for col_idx, width in widths.items():
            ws.column_dimensions[get_column_letter(col_idx)].width = width
    else:
        for col in range(1, n_cols + 1):
            max_len = max(
                (len(str(ws.cell(row=r, column=col).value or "")) for r in range(1, min(ws.max_row, 200) + 1)),
                default=8,
            )
            ws.column_dimensions[get_column_letter(col)].width = min(max_len + 4, 40)


def _alert_style(cell, es_roja: bool):
    cell.fill = RED_FILL if es_roja else AMBER_FILL
    cell.font = RED_FONT if es_roja else AMBER_FONT


def generate_excel(
    output: ReconciliationOutput,
    empresa_nombre: str,
    ruc: str,
    periodo: str,
    tipo_libro: str,
    propuesta_generada: datetime | None = None,
    cobertura: str | None = None,
    sin_sire: bool = False,
    meses_no_disponibles: list[str] | None = None,
) -> bytes:
    wb = Workbook()

    cnt_a = len(output.scenario_a)
    cnt_b = len(output.scenario_b)
    cnt_c = len(output.scenario_c)
    cnt_d = len(output.scenario_d)
    # Comprobantes reubicados por la modalidad "sin SIRE" (hallados en la
    # propuesta de otro mes que el conciliado).
    reubicados = (
        sum(1 for r in output.scenario_c if r.periodo_hallazgo)
        + sum(1 for r in output.scenario_d if r.periodo_hallazgo)
    )

    ws = wb.active
    ws.title = "Resumen"
    ws.sheet_view.showGridLines = False

    ws.merge_cells("B2:G3")
    title = ws["B2"]
    title.value = "REPORTE DE CONCILIACIÓN SIRE"
    title.fill = TITLE_FILL
    title.font = TITLE_FONT
    title.alignment = Alignment(horizontal="center", vertical="center")
    for row in ws["B2:G3"]:
        for c in row:
            c.fill = TITLE_FILL

    r = 5
    ws.cell(row=r, column=2, value="DATOS GENERALES").font = SECTION_FONT
    r += 1
    datos = [
        ("Empresa",       empresa_nombre),
        ("RUC",           ruc),
        ("Periodo",       f"{periodo[:4]}/{periodo[4:]}"),
        ("Tipo de libro", "Ventas (RVIE)" if tipo_libro == "ventas" else "Compras (RCE)"),
    ]
    if sin_sire:
        datos.append(("Modalidad", "Sin SIRE — búsqueda en propuestas de meses anteriores"))
    if cobertura is not None:
        datos.append(("Cobertura del archivo", cobertura))
    if propuesta_generada is not None:
        datos.append(("Propuesta SUNAT", f"generada el {propuesta_generada.astimezone().strftime('%d/%m/%Y %H:%M')}"))
    datos.append(("Generado", datetime.now().strftime("%d/%m/%Y %H:%M")))
    for label, value in datos:
        ws.cell(row=r, column=2, value=label).font = BOLD
        ws.cell(row=r, column=3, value=value)
        r += 1

    r += 1
    ws.cell(row=r, column=2, value="RESULTADOS").font = SECTION_FONT
    r += 1
    resultados = [
        ("A — En tu archivo, no en SUNAT",          cnt_a, "Comprobantes que declaraste pero SUNAT no tiene registrados en todo el mes", False),
        ("B — En SUNAT, no en tu archivo",          cnt_b, "Comprobantes que SUNAT tiene en las fechas de tu archivo y tú no enviaste", False),
        ("C — En ambos, con diferencias",           cnt_c, "Comprobantes encontrados en ambos pero con fecha o montos distintos", False),
        ("D — Coinciden sin diferencias",           cnt_d, "Comprobantes validados: idénticos en tu archivo y en SUNAT", True),
    ]
    for label, count, desc, es_bueno in resultados:
        ws.cell(row=r, column=2, value=label).font = BOLD
        cell_cnt = ws.cell(row=r, column=3, value=count)
        cell_cnt.font = Font(bold=True, size=12)
        cell_cnt.alignment = Alignment(horizontal="center")
        if count > 0:
            cell_cnt.fill = GREEN_FILL if es_bueno else AMBER_FILL
        ws.cell(row=r, column=4, value=desc).font = NOTE_FONT
        r += 1

    total_comparados = cnt_a + cnt_c + cnt_d
    if total_comparados:
        ws.cell(row=r, column=2, value="Tasa de conciliación").font = BOLD
        cell_tasa = ws.cell(row=r, column=3, value=cnt_d / total_comparados)
        cell_tasa.number_format = "0.00%"
        cell_tasa.font = Font(bold=True, size=12, color="006100")
        cell_tasa.alignment = Alignment(horizontal="center")
        ws.cell(row=r, column=4, value="Porcentaje de tus comprobantes que coinciden exactamente con SUNAT").font = NOTE_FONT
        r += 1

    r += 1
    ws.cell(row=r, column=2, value="Total diferencia de IGV").font = BOLD
    cell_igv = ws.cell(row=r, column=3, value=output.igv_diferencia_total)
    cell_igv.number_format = f'"S/" {NUM_FMT}'
    cell_igv.font = Font(bold=True, size=12)
    r += 1
    ws.cell(row=r, column=2, value="Alertas rojas").font = BOLD
    cell_al = ws.cell(row=r, column=3, value="SÍ" if output.tiene_alertas_rojas else "NO")
    cell_al.alignment = Alignment(horizontal="center")
    if output.tiene_alertas_rojas:
        _alert_style(cell_al, es_roja=True)
        cell_al.font = Font(bold=True, color="9C0006")
    r += 1

    r += 1
    ws.cell(row=r, column=2, value="LEYENDA").font = SECTION_FONT
    r += 1
    cell = ws.cell(row=r, column=2, value="ROJO")
    _alert_style(cell, es_roja=True)
    cell.alignment = Alignment(horizontal="center")
    ws.cell(row=r, column=3, value=f"Riesgo de IGV: falta o diferencia con impacto mayor a S/ {IGV_DIFF_THRESHOLD:.2f}. Revisar primero.")
    r += 1
    cell = ws.cell(row=r, column=2, value="ÁMBAR")
    _alert_style(cell, es_roja=False)
    cell.alignment = Alignment(horizontal="center")
    ws.cell(row=r, column=3, value="Revisar: inconsistencia sin impacto directo de IGV (fecha, montos sin IGV).")
    r += 1
    cell = ws.cell(row=r, column=2, value="Celda resaltada")
    cell.fill = DIFF_FILL
    cell.alignment = Alignment(horizontal="center")
    ws.cell(row=r, column=3, value="En la pestaña C: el valor de esa celda es el que difiere entre tu archivo y SUNAT.")
    r += 1

    notas = []
    if meses_no_disponibles:
        meses_txt = ", ".join(_fmt_periodo(m) for m in sorted(meses_no_disponibles))
        notas.append((
            f"⚠ No se pudo obtener la propuesta SUNAT de {len(meses_no_disponibles)} mes(es): {meses_txt}",
            "Sus comprobantes permanecen en el Escenario A SIN haberse verificado contra ese mes "
            "(SUNAT puede no tener la propuesta generada aún). No los tomes como faltantes "
            "confirmados hasta poder consultarlos.",
        ))
    if sin_sire:
        if reubicados:
            notas.append((
                f"Modalidad sin SIRE: {reubicados:,} comprobante(s) reubicados",
                "Estaban ausentes en la propuesta del periodo pero se hallaron en la de su "
                "mes de emisión; pasaron a C o D (ver columna «Hallado en periodo»). Los que "
                "no aparecieron en ningún mes permanecen en A.",
            ))
        else:
            notas.append((
                "Modalidad sin SIRE: sin reubicaciones",
                "Se buscaron los comprobantes del Escenario A en las propuestas de sus meses "
                "de emisión; ninguno se encontró, así que todos permanecen en A.",
            ))
    if output.total_excluidos:
        detalle = ", ".join(
            f"{TIPO_LABELS.get(t, t)} ({t}): {c:,}"
            for t, c in sorted(output.excluidos_por_tipo.items(), key=lambda x: -x[1])
        )
        notas.append((
            f"Registros de tu archivo excluidos de la comparación: {output.total_excluidos:,}",
            f"SUNAT no incluye estos tipos en la propuesta → {detalle}",
        ))
    if output.csv_duplicados:
        notas.append((
            f"Filas duplicadas en tu archivo: {output.csv_duplicados:,}",
            "Misma clave Tipo+Serie+Número repetida; se usó la última aparición.",
        ))
    if output.sunat_duplicados:
        notas.append((
            f"Filas duplicadas en la propuesta SUNAT: {output.sunat_duplicados:,}",
            "Se usó la última aparición.",
        ))
    # Cada hoja muestra hasta el máximo de filas de Excel; si un escenario lo
    # supera, se corta en la hoja y el listado completo va a un CSV descargable.
    for etiqueta, cnt in (("A", cnt_a), ("B", cnt_b), ("C", cnt_c), ("D", cnt_d)):
        if cnt > EXCEL_MAX_DATA_ROWS:
            notas.append((
                f"La pestaña {etiqueta} muestra {EXCEL_MAX_DATA_ROWS:,} de {cnt:,} registros",
                "Excel no admite más filas por hoja. El listado completo está en el CSV "
                f"descargable del Escenario {etiqueta}.",
            ))

    if notas:
        r += 1
        ws.cell(row=r, column=2, value="NOTAS").font = SECTION_FONT
        r += 1
        for nota, detalle in notas:
            ws.cell(row=r, column=2, value=nota).font = BOLD
            ws.cell(row=r, column=3, value=detalle).font = NOTE_FONT
            r += 1

    ws.column_dimensions["A"].width = 3
    ws.column_dimensions["B"].width = 38
    ws.column_dimensions["C"].width = 22
    ws.column_dimensions["D"].width = 70

    es_compras = tipo_libro == "compras"

    hay_estado = not es_compras and any(
        (rec.status_description or "").strip() for rec in output.scenario_a
    )

    ws_a = wb.create_sheet("A - Solo en tu archivo")
    if es_compras:
        _set_header_row(ws_a, [
            "Tipo", "Serie", "Número", "Fecha emisión", "RUC Proveedor", "Proveedor",
            "Base imponible", "IGV", "Importe total", "Alerta",
        ])
        col_alerta_a = 10
    elif hay_estado:
        _set_header_row(ws_a, [
            "Tipo", "Serie", "Número", "Fecha emisión",
            "Base imponible", "IGV", "Importe total", "Estado (tu archivo)", "Alerta",
        ])
        col_alerta_a = 9
    else:
        _set_header_row(ws_a, [
            "Tipo", "Serie", "Número", "Fecha emisión",
            "Base imponible", "IGV", "Importe total", "Alerta",
        ])
        col_alerta_a = 8
    use_fmt_a = cnt_a <= EXCEL_FORMAT_LIMIT
    for row_idx, rec in enumerate(output.scenario_a[:EXCEL_MAX_DATA_ROWS], 2):
        if es_compras:
            values = [
                rec.tipo_cdp, rec.serie, rec.numero, rec.fecha_emision,
                rec.ruc_proveedor, rec.razon_social,
                rec.base_imponible, rec.igv, rec.importe_total,
                "ROJO" if rec.es_alerta_roja else "ÁMBAR",
            ]
        elif hay_estado:
            values = [
                rec.tipo_cdp, rec.serie, rec.numero, rec.fecha_emision,
                rec.base_imponible, rec.igv, rec.importe_total,
                rec.status_description,
                "ROJO" if rec.es_alerta_roja else "ÁMBAR",
            ]
        else:
            values = [
                rec.tipo_cdp, rec.serie, rec.numero, rec.fecha_emision,
                rec.base_imponible, rec.igv, rec.importe_total,
                "ROJO" if rec.es_alerta_roja else "ÁMBAR",
            ]
        for col_idx, val in enumerate(values, 1):
            cell = ws_a.cell(row=row_idx, column=col_idx, value=val)
            if use_fmt_a:
                cell.border = THIN_BORDER
                if hay_estado and col_idx == 8 and "RECHAZ" in str(val).upper():
                    cell.font = Font(bold=True, color="9C0006")
                elif col_idx == col_alerta_a:
                    _alert_style(cell, rec.es_alerta_roja)
                    cell.alignment = Alignment(horizontal="center")
    _finish_sheet(ws_a, col_alerta_a, num_cols=[7, 8, 9] if es_compras else [5, 6, 7])

    ws_b = wb.create_sheet("B - Solo en SUNAT")
    if es_compras:
        _set_header_row(ws_b, [
            "Tipo", "Serie", "Número", "Fecha emisión SUNAT", "RUC Proveedor", "Proveedor",
            "Base imponible", "IGV", "Importe total", "Alerta",
        ])
    else:
        _set_header_row(ws_b, [
            "Tipo", "Serie", "Número", "Fecha emisión SUNAT",
            "Base imponible", "IGV", "Importe total", "Alerta",
        ])
    recs_b_sorted = sorted(output.scenario_b, key=lambda x: (not x.es_alerta_roja, -x.igv_sunat))
    recs_b = recs_b_sorted[:EXCEL_MAX_DATA_ROWS]
    use_fmt_b = len(recs_b) <= EXCEL_FORMAT_LIMIT
    col_alerta_b = 10 if es_compras else 8

    for row_idx, rec in enumerate(recs_b, 2):
        if es_compras:
            values = [
                rec.tipo_cdp, rec.serie, rec.numero, rec.fecha_emision,
                rec.ruc_proveedor, rec.razon_social,
                rec.base_imponible_sunat, rec.igv_sunat, rec.importe_total_sunat,
                "ROJO" if rec.es_alerta_roja else "ÁMBAR",
            ]
        else:
            values = [
                rec.tipo_cdp, rec.serie, rec.numero, rec.fecha_emision,
                rec.base_imponible_sunat, rec.igv_sunat, rec.importe_total_sunat,
                "ROJO" if rec.es_alerta_roja else "ÁMBAR",
            ]
        for col_idx, val in enumerate(values, 1):
            cell = ws_b.cell(row=row_idx, column=col_idx, value=val)
            if use_fmt_b:
                cell.border = THIN_BORDER
                if col_idx == col_alerta_b:
                    _alert_style(cell, rec.es_alerta_roja)
                    cell.alignment = Alignment(horizontal="center")
    _finish_sheet(ws_b, col_alerta_b, num_cols=[7, 8, 9] if es_compras else [5, 6, 7])

    ws_c = wb.create_sheet("C - Diferencias")
    use_fmt_c = cnt_c <= EXCEL_FORMAT_LIMIT

    CAMPO_LABELS = {
        "fecha": "Fecha", "base_imponible": "Base imponible",
        "igv": "IGV", "importe_total": "Importe total",
        "mto_exonerado": "Exonerado", "mto_inafecto": "Inafecto",
        "bi_dgng": "BI DGNG", "igv_dgng": "IGV DGNG",
        "bi_dng": "BI DNG", "igv_dng": "IGV DNG",
        "valor_adq_ng": "Adq. no gravadas", "moneda": "Moneda", "tipo_cambio": "Tipo de cambio",
    }

    # Solo compras "sin SIRE": comprobantes reubicados en la propuesta de otro mes.
    hay_hallazgo_c = es_compras and any(r.periodo_hallazgo for r in output.scenario_c)
    hay_hallazgo_d = es_compras and any(r.periodo_hallazgo for r in output.scenario_d)

    if es_compras:
        headers_c = [
            "Tipo", "Serie", "Número", "RUC Proveedor", "Proveedor",
            "Fecha (tu archivo)", "Fecha (SUNAT)",
            "BI DG (tu archivo)", "BI DG (SUNAT)",
            "IGV DG (tu archivo)", "IGV DG (SUNAT)",
            "BI DGNG (tu archivo)", "BI DGNG (SUNAT)",
            "IGV DGNG (tu archivo)", "IGV DGNG (SUNAT)",
            "BI DNG (tu archivo)", "BI DNG (SUNAT)",
            "IGV DNG (tu archivo)", "IGV DNG (SUNAT)",
            "Adq. NG (tu archivo)", "Adq. NG (SUNAT)",
            "Total (tu archivo)", "Total (SUNAT)",
            "Moneda (tu archivo)", "Moneda (SUNAT)",
            "TC (tu archivo)", "TC (SUNAT)",
            "Campos con diferencia", "Alerta",
        ]
        if hay_hallazgo_c:
            headers_c.append("Hallado en periodo")
        _set_header_row(ws_c, headers_c)
        CAMPO_COLS = {
            "fecha": (6, 7), "base_imponible": (8, 9), "igv": (10, 11),
            "bi_dgng": (12, 13), "igv_dgng": (14, 15),
            "bi_dng": (16, 17), "igv_dng": (18, 19),
            "valor_adq_ng": (20, 21), "importe_total": (22, 23),
            "moneda": (24, 25), "tipo_cambio": (26, 27),
        }
        CAMPO_ORDER = [
            "fecha", "base_imponible", "igv", "bi_dgng", "igv_dgng",
            "bi_dng", "igv_dng", "valor_adq_ng", "importe_total", "moneda", "tipo_cambio",
        ]
        n_cols_c = 29
        num_cols_c = list(range(8, 24)) + [26, 27]
    else:
        _set_header_row(ws_c, [
            "Tipo", "Serie", "Número",
            "Fecha (tu archivo)", "Fecha (SUNAT)",
            "Base imp. (tu archivo)", "Base imp. (SUNAT)",
            "IGV (tu archivo)", "IGV (SUNAT)",
            "Total (tu archivo)", "Total (SUNAT)",
            "Exonerado (tu archivo)", "Exonerado (SUNAT)",
            "Inafecto (tu archivo)", "Inafecto (SUNAT)",
            "Campos con diferencia", "Alerta",
        ])
        CAMPO_COLS = {
            "fecha":          (4, 5),
            "base_imponible": (6, 7),
            "igv":            (8, 9),
            "importe_total":  (10, 11),
            "mto_exonerado":  (12, 13),
            "mto_inafecto":   (14, 15),
        }
        CAMPO_ORDER = ["fecha", "base_imponible", "igv", "importe_total", "mto_exonerado", "mto_inafecto"]
        n_cols_c = 17
        num_cols_c = [6, 7, 8, 9, 10, 11, 12, 13, 14, 15]

    for row_idx, rec in enumerate(output.scenario_c[:EXCEL_MAX_DATA_ROWS], 2):
        campos = rec.campos_diferentes
        campos_txt = ", ".join(CAMPO_LABELS[c] for c in CAMPO_ORDER if c in campos)

        if es_compras:
            values = [
                rec.tipo_cdp, rec.serie, rec.numero, rec.ruc_proveedor, rec.razon_social,
                rec.fecha_emision_empresa, rec.fecha_emision_sunat,
                rec.base_imponible_empresa, rec.base_imponible_sunat,
                rec.igv_empresa, rec.igv_sunat,
                rec.bi_dgng_empresa, rec.bi_dgng_sunat,
                rec.igv_dgng_empresa, rec.igv_dgng_sunat,
                rec.bi_dng_empresa, rec.bi_dng_sunat,
                rec.igv_dng_empresa, rec.igv_dng_sunat,
                rec.valor_adq_ng_empresa, rec.valor_adq_ng_sunat,
                rec.importe_total_empresa, rec.importe_total_sunat,
                rec.moneda_empresa, rec.moneda_sunat,
                rec.tipo_cambio_empresa, rec.tipo_cambio_sunat,
                campos_txt,
                "ROJO" if rec.es_alerta_roja else "ÁMBAR",
            ]
        else:
            values = [
                rec.tipo_cdp, rec.serie, rec.numero,
                rec.fecha_emision_empresa, rec.fecha_emision_sunat,
                rec.base_imponible_empresa, rec.base_imponible_sunat,
                rec.igv_empresa, rec.igv_sunat,
                rec.importe_total_empresa, rec.importe_total_sunat,
                rec.mto_exonerado_empresa, rec.mto_exonerado_sunat,
                rec.mto_inafecto_empresa, rec.mto_inafecto_sunat,
                campos_txt,
                "ROJO" if rec.es_alerta_roja else "ÁMBAR",
            ]
        if hay_hallazgo_c:
            values.append(_fmt_periodo(rec.periodo_hallazgo))
        diff_cols = {col for campo in campos for col in CAMPO_COLS.get(campo, ())}

        for col_idx, val in enumerate(values, 1):
            cell = ws_c.cell(row=row_idx, column=col_idx, value=val)
            if use_fmt_c:
                cell.border = THIN_BORDER
                if col_idx in diff_cols:
                    cell.fill = DIFF_FILL
                    cell.font = BOLD
                elif col_idx == n_cols_c:
                    _alert_style(cell, rec.es_alerta_roja)
                    cell.alignment = Alignment(horizontal="center")
    _finish_sheet(ws_c, n_cols_c, num_cols=num_cols_c)

    ws_d = wb.create_sheet("D - Coinciden OK")
    if es_compras:
        headers_d = [
            "Tipo", "Serie", "Número", "RUC Proveedor", "Proveedor",
            "Fecha (tu archivo)", "Fecha (SUNAT)",
            "BI DG (tu archivo)", "BI DG (SUNAT)",
            "IGV DG (tu archivo)", "IGV DG (SUNAT)",
            "Total (tu archivo)", "Total (SUNAT)",
            "Moneda (tu archivo)", "Moneda (SUNAT)",
            "TC (tu archivo)", "TC (SUNAT)",
        ]
        if hay_hallazgo_d:
            headers_d.append("Hallado en periodo")
        _set_header_row(ws_d, headers_d)
        n_cols_d = 17
        num_cols_d = [8, 9, 10, 11, 12, 13, 16, 17]
    else:
        _set_header_row(ws_d, [
            "Tipo", "Serie", "Número",
            "Fecha (tu archivo)", "Fecha (SUNAT)",
            "Base imp. (tu archivo)", "Base imp. (SUNAT)",
            "IGV (tu archivo)", "IGV (SUNAT)",
            "Total (tu archivo)", "Total (SUNAT)",
            "Exonerado (tu archivo)", "Exonerado (SUNAT)",
            "Inafecto (tu archivo)", "Inafecto (SUNAT)",
        ])
        n_cols_d = 15
        num_cols_d = [6, 7, 8, 9, 10, 11, 12, 13, 14, 15]
    use_fmt_d = cnt_d <= EXCEL_FORMAT_LIMIT
    recs_d = output.scenario_d[:EXCEL_MAX_DATA_ROWS]
    for row_idx, rec in enumerate(recs_d, 2):
        if es_compras:
            values = [
                rec.tipo_cdp, rec.serie, rec.numero, rec.ruc_proveedor, rec.razon_social,
                rec.fecha_emision_empresa, rec.fecha_emision_sunat,
                rec.base_imponible_empresa, rec.base_imponible_sunat,
                rec.igv_empresa, rec.igv_sunat,
                rec.importe_total_empresa, rec.importe_total_sunat,
                rec.moneda_empresa, rec.moneda_sunat,
                rec.tipo_cambio_empresa, rec.tipo_cambio_sunat,
            ]
        else:
            values = [
                rec.tipo_cdp, rec.serie, rec.numero,
                rec.fecha_emision_empresa, rec.fecha_emision_sunat,
                rec.base_imponible_empresa, rec.base_imponible_sunat,
                rec.igv_empresa, rec.igv_sunat,
                rec.importe_total_empresa, rec.importe_total_sunat,
                rec.mto_exonerado_empresa, rec.mto_exonerado_sunat,
                rec.mto_inafecto_empresa, rec.mto_inafecto_sunat,
            ]
        if hay_hallazgo_d:
            values.append(_fmt_periodo(rec.periodo_hallazgo))
        for col_idx, val in enumerate(values, 1):
            cell = ws_d.cell(row=row_idx, column=col_idx, value=val)
            if use_fmt_d:
                cell.border = THIN_BORDER
    _finish_sheet(ws_d, n_cols_d, num_cols=num_cols_d if use_fmt_d else None)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def generate_csv_a(output: ReconciliationOutput, tipo_libro: str, out) -> None:
    """CSV completo del Escenario A — solo se genera si A supera el máximo de Excel."""
    es_compras = tipo_libro == "compras"
    w = csv.writer(out)
    if es_compras:
        w.writerow([
            "Tipo CDP", "Serie", "Numero", "Fecha Emision", "RUC Proveedor", "Proveedor",
            "Base Imponible", "IGV", "Importe Total", "Alerta",
        ])
        for rec in output.scenario_a:
            w.writerow([
                rec.tipo_cdp, rec.serie, rec.numero, rec.fecha_emision,
                rec.ruc_proveedor, rec.razon_social,
                rec.base_imponible, rec.igv, rec.importe_total,
                "ROJO" if rec.es_alerta_roja else "AMBAR",
            ])
    else:
        w.writerow([
            "Tipo CDP", "Serie", "Numero", "Fecha Emision", "Estado (tu archivo)",
            "Base Imponible", "IGV", "Importe Total", "Alerta",
        ])
        for rec in output.scenario_a:
            w.writerow([
                rec.tipo_cdp, rec.serie, rec.numero, rec.fecha_emision, rec.status_description,
                rec.base_imponible, rec.igv, rec.importe_total,
                "ROJO" if rec.es_alerta_roja else "AMBAR",
            ])


def generate_csv_b(output: ReconciliationOutput, tipo_libro: str, out) -> None:
    """CSV completo del Escenario B — solo se genera si B supera el máximo de Excel."""
    es_compras = tipo_libro == "compras"
    w = csv.writer(out)
    if es_compras:
        w.writerow([
            "Tipo CDP", "Serie", "Numero", "Fecha Emision SUNAT", "RUC Proveedor", "Proveedor",
            "Base Imponible", "IGV", "Importe Total", "Alerta",
        ])
        for rec in output.scenario_b:
            w.writerow([
                rec.tipo_cdp, rec.serie, rec.numero, rec.fecha_emision,
                rec.ruc_proveedor, rec.razon_social,
                rec.base_imponible_sunat, rec.igv_sunat, rec.importe_total_sunat,
                "ROJO" if rec.es_alerta_roja else "AMBAR",
            ])
    else:
        w.writerow([
            "Tipo CDP", "Serie", "Numero", "Fecha Emision SUNAT",
            "Base Imponible", "IGV", "Importe Total", "Alerta",
        ])
        for rec in output.scenario_b:
            w.writerow([
                rec.tipo_cdp, rec.serie, rec.numero, rec.fecha_emision,
                rec.base_imponible_sunat, rec.igv_sunat, rec.importe_total_sunat,
                "ROJO" if rec.es_alerta_roja else "AMBAR",
            ])


def generate_csv_c(output: ReconciliationOutput, tipo_libro: str, out) -> None:
    """CSV completo del Escenario C — solo se genera si C supera el máximo de Excel."""
    es_compras = tipo_libro == "compras"
    hay_hallazgo = es_compras and any(r.periodo_hallazgo for r in output.scenario_c)
    labels = {
        "fecha": "Fecha", "base_imponible": "Base imponible", "igv": "IGV",
        "importe_total": "Importe total", "mto_exonerado": "Exonerado", "mto_inafecto": "Inafecto",
        "bi_dgng": "BI DGNG", "igv_dgng": "IGV DGNG", "bi_dng": "BI DNG", "igv_dng": "IGV DNG",
        "valor_adq_ng": "Adq. no gravadas", "moneda": "Moneda", "tipo_cambio": "Tipo de cambio",
    }
    orden = ["fecha", "base_imponible", "igv", "bi_dgng", "igv_dgng", "bi_dng", "igv_dng",
             "valor_adq_ng", "importe_total", "moneda", "tipo_cambio",
             "mto_exonerado", "mto_inafecto"]

    def campos_txt(rec):
        cs = rec.campos_diferentes
        return ", ".join(labels[c] for c in orden if c in cs)

    w = csv.writer(out)
    if es_compras:
        header = [
            "Tipo CDP", "Serie", "Numero", "RUC Proveedor", "Proveedor",
            "Fecha (Empresa)", "Fecha (SUNAT)",
            "BI DG (Empresa)", "BI DG (SUNAT)", "IGV DG (Empresa)", "IGV DG (SUNAT)",
            "BI DGNG (Empresa)", "BI DGNG (SUNAT)", "IGV DGNG (Empresa)", "IGV DGNG (SUNAT)",
            "BI DNG (Empresa)", "BI DNG (SUNAT)", "IGV DNG (Empresa)", "IGV DNG (SUNAT)",
            "Adq. NG (Empresa)", "Adq. NG (SUNAT)", "Total (Empresa)", "Total (SUNAT)",
            "Moneda (Empresa)", "Moneda (SUNAT)", "TC (Empresa)", "TC (SUNAT)",
            "Campos con diferencia", "Alerta",
        ]
        if hay_hallazgo:
            header.append("Hallado en periodo")
        w.writerow(header)
        for rec in output.scenario_c:
            fila = [
                rec.tipo_cdp, rec.serie, rec.numero, rec.ruc_proveedor, rec.razon_social,
                rec.fecha_emision_empresa, rec.fecha_emision_sunat,
                rec.base_imponible_empresa, rec.base_imponible_sunat,
                rec.igv_empresa, rec.igv_sunat,
                rec.bi_dgng_empresa, rec.bi_dgng_sunat, rec.igv_dgng_empresa, rec.igv_dgng_sunat,
                rec.bi_dng_empresa, rec.bi_dng_sunat, rec.igv_dng_empresa, rec.igv_dng_sunat,
                rec.valor_adq_ng_empresa, rec.valor_adq_ng_sunat,
                rec.importe_total_empresa, rec.importe_total_sunat,
                rec.moneda_empresa, rec.moneda_sunat,
                rec.tipo_cambio_empresa, rec.tipo_cambio_sunat,
                campos_txt(rec), "ROJO" if rec.es_alerta_roja else "AMBAR",
            ]
            if hay_hallazgo:
                fila.append(_fmt_periodo(rec.periodo_hallazgo))
            w.writerow(fila)
    else:
        w.writerow([
            "Tipo CDP", "Serie", "Numero",
            "Fecha (Empresa)", "Fecha (SUNAT)",
            "Base Imponible (Empresa)", "Base Imponible (SUNAT)",
            "IGV (Empresa)", "IGV (SUNAT)", "Importe Total (Empresa)", "Importe Total (SUNAT)",
            "Exonerado (Empresa)", "Exonerado (SUNAT)", "Inafecto (Empresa)", "Inafecto (SUNAT)",
            "Campos con diferencia", "Alerta",
        ])
        for rec in output.scenario_c:
            w.writerow([
                rec.tipo_cdp, rec.serie, rec.numero,
                rec.fecha_emision_empresa, rec.fecha_emision_sunat,
                rec.base_imponible_empresa, rec.base_imponible_sunat,
                rec.igv_empresa, rec.igv_sunat,
                rec.importe_total_empresa, rec.importe_total_sunat,
                rec.mto_exonerado_empresa, rec.mto_exonerado_sunat,
                rec.mto_inafecto_empresa, rec.mto_inafecto_sunat,
                campos_txt(rec), "ROJO" if rec.es_alerta_roja else "AMBAR",
            ])


def generate_csv_d(output: ReconciliationOutput, tipo_libro: str, out) -> None:
    """CSV completo del Escenario D — solo se genera si D supera el máximo de Excel."""
    es_compras = tipo_libro == "compras"
    hay_hallazgo = es_compras and any(r.periodo_hallazgo for r in output.scenario_d)
    w = csv.writer(out)
    if es_compras:
        header = [
            "Tipo CDP", "Serie", "Numero", "RUC Proveedor", "Proveedor",
            "Fecha (Empresa)", "Fecha (SUNAT)",
            "BI DG (Empresa)", "BI DG (SUNAT)",
            "IGV DG (Empresa)", "IGV DG (SUNAT)",
            "Importe Total (Empresa)", "Importe Total (SUNAT)",
            "Moneda (Empresa)", "Moneda (SUNAT)",
            "TC (Empresa)", "TC (SUNAT)",
        ]
        if hay_hallazgo:
            header.append("Hallado en periodo")
        w.writerow(header)
        for rec in output.scenario_d:
            fila = [
                rec.tipo_cdp, rec.serie, rec.numero, rec.ruc_proveedor, rec.razon_social,
                rec.fecha_emision_empresa, rec.fecha_emision_sunat,
                rec.base_imponible_empresa, rec.base_imponible_sunat,
                rec.igv_empresa, rec.igv_sunat,
                rec.importe_total_empresa, rec.importe_total_sunat,
                rec.moneda_empresa, rec.moneda_sunat,
                rec.tipo_cambio_empresa, rec.tipo_cambio_sunat,
            ]
            if hay_hallazgo:
                fila.append(_fmt_periodo(rec.periodo_hallazgo))
            w.writerow(fila)
    else:
        w.writerow([
            "Tipo CDP", "Serie", "Numero",
            "Fecha (Empresa)", "Fecha (SUNAT)",
            "Base Imponible (Empresa)", "Base Imponible (SUNAT)",
            "IGV (Empresa)", "IGV (SUNAT)",
            "Importe Total (Empresa)", "Importe Total (SUNAT)",
            "Exonerado (Empresa)", "Exonerado (SUNAT)",
            "Inafecto (Empresa)", "Inafecto (SUNAT)",
        ])
        for rec in output.scenario_d:
            w.writerow([
                rec.tipo_cdp, rec.serie, rec.numero,
                rec.fecha_emision_empresa, rec.fecha_emision_sunat,
                rec.base_imponible_empresa, rec.base_imponible_sunat,
                rec.igv_empresa, rec.igv_sunat,
                rec.importe_total_empresa, rec.importe_total_sunat,
                rec.mto_exonerado_empresa, rec.mto_exonerado_sunat,
                rec.mto_inafecto_empresa, rec.mto_inafecto_sunat,
            ])
