"""
Genera un Excel (.xlsx) de los documentos/registros del Scanner a partir de filas
ya aplanadas por el frontend (una fila por documento, o por registro en las
planillas multi-registro). Reusa openpyxl, que ya es dependencia del proyecto.
"""
import io

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

_HEADER_FILL = PatternFill("solid", fgColor="FF0E4F4A")
_HEADER_FONT = Font(bold=True, color="FFFFFFFF")


def _valor(v: object):
    """Deja números como números (para que Excel los sume); el resto como texto."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Sí" if v else "No"
    if isinstance(v, (int, float)):
        return v
    return str(v)


def generar_excel(filas: list[dict], columnas: list[str], labels: dict[str, str]) -> bytes:
    """Arma el .xlsx: columna 'Archivo' + las columnas indicadas (con sus etiquetas).
    Cabecera en negrita, filtro automático y primera fila congelada."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Documentos"

    encabezados = ["Archivo"] + [labels.get(c, c) for c in columnas]
    ws.append(encabezados)
    for cell in ws[1]:
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL

    for fila in filas:
        ws.append([str(fila.get("archivo", ""))] + [_valor(fila.get(c)) for c in columnas])

    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions
    for i in range(1, len(encabezados) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 22

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
