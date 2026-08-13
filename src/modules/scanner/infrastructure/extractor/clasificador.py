"""
clasificador.py — Detecta el tipo de documento desde su contenido de texto.
No usa el nombre del archivo: se basa solo en palabras clave, series y patrones SUNAT.
"""
import re

# ── Constantes de tipo ──────────────────────────────────────────────────────
TIPO_FACTURA_ELECTRONICA = "factura_electronica"
TIPO_BOLETA_VENTA        = "boleta_venta"
TIPO_RECIBO_HONORARIOS   = "recibo_honorarios"
TIPO_NOTA_CREDITO        = "nota_credito"
TIPO_NOTA_DEBITO         = "nota_debito"
TIPO_RECIBO_LUZ          = "recibo_luz"
TIPO_RECIBO_AGUA         = "recibo_agua"
TIPO_RECIBO_GAS          = "recibo_gas"
TIPO_RECIBO_TELEFONIA    = "recibo_telefonia"
TIPO_ASISTENCIA          = "asistencia"
TIPO_DESCONOCIDO         = "desconocido"

# Etiquetas amigables para el frontend
ETIQUETAS = {
    TIPO_FACTURA_ELECTRONICA: "Factura Electrónica",
    TIPO_BOLETA_VENTA:        "Boleta de Venta",
    TIPO_RECIBO_HONORARIOS:   "Recibo por Honorarios",
    TIPO_NOTA_CREDITO:        "Nota de Crédito",
    TIPO_NOTA_DEBITO:         "Nota de Débito",
    TIPO_RECIBO_LUZ:          "Recibo de Luz",
    TIPO_RECIBO_AGUA:         "Recibo de Agua",
    TIPO_RECIBO_GAS:          "Recibo de Gas",
    TIPO_RECIBO_TELEFONIA:    "Recibo de Telefonía / Internet",
    TIPO_ASISTENCIA:          "Planilla de Asistencia",
    TIPO_DESCONOCIDO:         "Documento desconocido",
}

# ── Reglas de clasificación ──────────────────────────────────────────────────
# Cada regla: (tipo, [patrones_regex], peso)
# Mayor peso = señal más fuerte y específica.
# Un tipo puede tener varias reglas; los scores se suman.
_REGLAS: list[tuple[str, list[str], int]] = [

    # ── Comprobantes SUNAT ──────────────────────────────────────────────────
    (TIPO_FACTURA_ELECTRONICA, [
        r"FACTURA\s+ELECTR[OÓ]NICA",
        r"\bF\d{3}\s*[-–]\s*\d{5,}",          # F001-00000087
        r"FACTURA\s+N[°º]?\s*F\d{3}",
    ], 12),

    (TIPO_BOLETA_VENTA, [
        r"BOLETA\s+DE\s+VENTA",
        r"\bB\d{3}\s*[-–]\s*\d{5,}",          # B001-00000123
    ], 12),

    (TIPO_RECIBO_HONORARIOS, [
        r"RECIBO\s+POR\s+HONORARIOS",
        r"HONORARIOS\s+PROFESIONALES",
        r"\bE\d{3}\s*[-–]\s*\d{5,}",          # E001-00000001
        r"RETENCI[OÓ]N\s+(?:DEL?\s+)?4\s*%",
        r"NETO\s+A\s+PAGAR",
    ], 10),

    (TIPO_NOTA_CREDITO, [
        r"NOTA\s+DE\s+CR[EÉ]DITO\s+ELECTR[OÓ]NICA",
        r"NOTA\s+DE\s+CR[EÉ]DITO",
        r"\bNC\d{2}\s*[-–]\s*\d{5,}",         # NC01-00000012
    ], 12),

    (TIPO_NOTA_DEBITO, [
        r"NOTA\s+DE\s+D[EÉ]BITO\s+ELECTR[OÓ]NICA",
        r"NOTA\s+DE\s+D[EÉ]BITO",
        r"\bND\d{2}\s*[-–]\s*\d{5,}",         # ND01-00000012
    ], 12),

    # ── Recibo de electricidad ───────────────────────────────────────────────
    (TIPO_RECIBO_LUZ, [
        r"\bkWh\b",
        r"N[°º]?\s*(?:DE\s+)?SUMINISTRO",
        r"LECTURA\s+(?:ACTUAL|ANTERIOR)",
        r"CONSUMO\s+(?:DE\s+)?ENERG[IÍ]A",
        r"ALUMBRADO\s+P[UÚ]BLICO",
        r"LUZ\s+DEL\s+SUR|ENEL\b|HIDRANDINA|ELECTROCENTRO|ELECTRO\s+NORTE|"
        r"SEAL\b|ELSE\b|PLUZ\b|ENOSA\b|ELECTRO\s+ORIENTE|ELECTRO\s+SUR\s+ESTE|"
        r"ELECTRO\s+PUNO|ELECTRO\s+UCAYALI|ADINELSA|EDELNOR",
    ], 8),

    # ── Recibo de agua ───────────────────────────────────────────────────────
    (TIPO_RECIBO_AGUA, [
        r"SEDAPAL|SEDACHIMBOTE|SEDA\s+CUSCO|SEDALIB|SEDAPAR|SEDACUSCO|"
        r"EMFAPATUMBES|AGUAS\s+DE\s+(?:LIMA|AREQUIPA|TUMBES)|EPS\s+\w+",
        r"AGUA\s+POTABLE",
        r"CONSUMO\s+DE\s+AGUA",
        r"ALCANTARILLADO",
        r"M[3³]\s*(?:AGUA|CONSUMO)|CONSUMO.*M[3³]",
        r"N[°º]?\s*(?:DE\s+)?CUENTA.*AGUA|CUENTA\s+DE\s+AGUA",
    ], 8),

    # ── Recibo de gas ────────────────────────────────────────────────────────
    (TIPO_RECIBO_GAS, [
        r"C[AÁ]LIDDA|GASES\s+DEL\s+PAC[IÍ]FICO|QUAVII|CONTUGAS|GNC\s+PERU",
        r"GAS\s+NATURAL",
        r"CONSUMO\s+DE\s+GAS",
        r"PRESI[OÓ]N\s+(?:DE\s+)?(?:SERVICIO|GAS)",
        r"M[3³]\s*(?:GAS|CONSUMO\s+GAS)|GAS.*M[3³]",
        r"N[°º]?\s*(?:DE\s+)?CONTRATO.*GAS",
    ], 8),

    # ── Recibo de telefonía / internet ───────────────────────────────────────
    (TIPO_RECIBO_TELEFONIA, [
        r"\bCLARO\b|\bMOVISTAR\b|\bENTEL\b|\bBITEL\b|\bWOM\b|\bVIRGIN\s+MOBILE\b",
        r"N[°º]?\s*(?:DE\s+)?L[IÍ]NEA",
        r"PLAN\s+(?:TARIF|M[OÓ]VIL|FIJ|DATOS|ILIMITADO)",
        r"DATOS\s+M[OÓ]VILES|MB\s+CONSUMIDO|GB\s+CONSUMIDO",
        r"ROAMING",
        r"TELEFON[IÍ]A\s+(?:M[OÓ]VIL|FIJA)|INTERNET\s+(?:M[OÓ]VIL|FIJO)",
    ], 8),

    # ── Planilla de asistencia ───────────────────────────────────────────────
    (TIPO_ASISTENCIA, [
        r"PLANILLA\s+(?:DE\s+)?ASISTENCIA",
        r"CONTROL\s+(?:DE\s+)?ASISTENCIA",
        r"REGISTRO\s+(?:DE\s+)?ASISTENCIA",
        r"HORA\s+(?:DE\s+)?(?:ENTRADA|INGRESO).*HORA\s+(?:DE\s+)?SALIDA",
        r"(?:ENTRADA|INGRESO)\s+(?:SALIDA|EGRESO).*DNI",
        r"FIRMA\s+(?:DEL?\s+)?TRABAJADOR",
    ], 8),
]


def clasificar(texto: str) -> tuple[str, float]:
    """
    Detecta el tipo de documento a partir de su contenido.

    Retorna:
        (tipo_documento: str, confianza: float)  — confianza entre 0.0 y 1.0

    La confianza refleja qué porcentaje del score total corresponde
    al tipo ganador: alta = el tipo está claro, baja = ambiguo.
    """
    texto_upper = texto.upper()
    scores: dict[str, int] = {}

    for tipo, patrones, peso in _REGLAS:
        hits = sum(1 for p in patrones if re.search(p, texto_upper))
        if hits > 0:
            scores[tipo] = scores.get(tipo, 0) + hits * peso

    if not scores:
        return TIPO_DESCONOCIDO, 0.0

    mejor = max(scores, key=scores.get)
    total = sum(scores.values())
    confianza = round(scores[mejor] / total, 2)

    return mejor, confianza


def etiqueta(tipo: str) -> str:
    """Nombre legible del tipo para mostrar en el frontend."""
    return ETIQUETAS.get(tipo, tipo)
