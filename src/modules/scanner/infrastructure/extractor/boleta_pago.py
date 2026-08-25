"""
boleta_pago.py — Extrae registros de boletas de pago de remuneraciones (nómina).

Un PDF suele traer una boleta por página (un trabajador cada una), con el bloque
repetido (copia empleador + copia trabajador). Se extrae un registro por página
(`re.search` toma la primera aparición → valores correctos aunque se repita).

Campos por registro:
  empresa, ruc_empresa, periodo, trabajador, dni, cargo, codigo, fecha_ingreso,
  dias_trabajados, asegurado_afp, basico, total_ingresos, total_descuento,
  total_aporte, neto_pagar
"""
import os
import re

_MONTO = r"([\d.,]+\.\d{2})"


def extract_boleta_pago(file_path: str) -> list:
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        import pymupdf
        doc = pymupdf.open(file_path)
        registros = []
        for page in doc:
            r = _parsear(page.get_text())
            if r.get("trabajador") or r.get("dni"):
                registros.append(r)
        doc.close()
        return registros
    # Imagen u otro formato: un solo registro desde el texto OCR.
    from src.modules.scanner.infrastructure.utils import leer_archivo
    r = _parsear(leer_archivo(file_path))
    return [r] if (r.get("trabajador") or r.get("dni")) else []


def _valor(texto: str, label_re: str, valor_re: str = r"([A-ZÁÉÍÓÚÑ0-9][^\n]+)") -> str | None:
    """Captura el valor de un campo 'Etiqueta : valor' (misma línea o la siguiente)."""
    m = re.search(rf"{label_re}\s*:?\s*{valor_re}", texto, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _monto(texto: str, label_re: str) -> float | None:
    m = re.search(rf"{label_re}\s*{_MONTO}", texto, re.IGNORECASE)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def _parsear(texto: str) -> dict:
    lineas = [ln.strip() for ln in texto.splitlines() if ln.strip()]
    empresa = lineas[0] if lineas else None

    descuento = _monto(texto, r"TOTAL\s+DESCUENTO\s+S/\.")
    neto = _monto(texto, r"NETO\s+S/\.")
    ingresos = _monto(texto, r"TOTAL\s+INGRESOS\s+S/\.")
    if ingresos is None:
        # El número de ingresos suele quedar justo antes de "TOTAL DESCUENTO".
        m = re.search(rf"{_MONTO}\s*\n\s*TOTAL\s+DESCUENTO", texto, re.IGNORECASE)
        if m:
            ingresos = float(m.group(1).replace(",", ""))
        elif neto is not None and descuento is not None:
            ingresos = round(neto + descuento, 2)

    return {
        "empresa":         empresa,
        "ruc_empresa":     _valor(texto, r"RUC\.?", r"(\d{11})"),
        "periodo":         _valor(texto, r"MES", r"([A-ZÑÁÉÍÓÚ]+[-\s]\d{4})"),
        "trabajador":      _valor(texto, r"Nombre"),
        "dni":             _valor(texto, r"N[°º]?\s*DNI", r"(\d{8})"),
        "cargo":           _valor(texto, r"Cargo"),
        "codigo":          _valor(texto, r"C[oó]digo", r"([A-Z0-9]{2,})"),
        "fecha_ingreso":   _valor(texto, r"Fecha\s+Ingreso", r"(\d{2}/\d{2}/\d{4})"),
        "dias_trabajados": _valor(texto, r"D[ií]as\s+Efectivos\s+Trabajados", r"(\d{1,3})"),
        "asegurado_afp":   _valor(texto, r"Asegurado\s+en", r"([A-ZÑ][A-ZÑ ]+)"),
        "basico":          _monto(texto, r"B[aá]sico\s+Mes\s*:?"),
        "total_ingresos":  ingresos,
        "total_descuento": descuento,
        "total_aporte":    _monto(texto, r"TOTAL\s+APORTE\s+S/\."),
        "neto_pagar":      neto,
    }
