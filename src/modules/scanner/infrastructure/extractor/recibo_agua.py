"""
recibo_agua.py — Extrae campos de recibos de agua (EPS/saneamiento) peruanas.
Cubre: SEDAPAL, SEDACHIMBOTE, SEDA CUSCO, SEDALIB, SEDAPAR,
       EMFAPATUMBES, AGUAS DE LIMA y otras EPS.
"""
import re
from src.modules.scanner.infrastructure.utils import (
    extraer_todos_los_ruc, extraer_monto, normalizar_fecha,
    extraer_mes, extraer_anio,
    extraer_fecha_vencimiento, leer_archivo,
)

EMPRESAS_AGUA = [
    "SEDAPAL", "SEDACHIMBOTE", "SEDA CUSCO", "SEDACUSCO", "SEDALIB",
    "SEDAPAR", "EMFAPATUMBES", "AGUAS DE LIMA", "EPS GRAU", "EPS ILO",
    "EPS TACNA", "EMAPA HUACHO", "EMAPA PASCO", "EMAPA HUANCAVELICA",
    "EMSAP", "SEMAPACH",
]


def extract_recibo_agua(file_path: str) -> dict:
    return _parsear(leer_archivo(file_path))


def _parsear(texto: str) -> dict:
    rucs = extraer_todos_los_ruc(texto)
    return {
        "empresa":             _empresa(texto),
        "ruc_empresa":         rucs[0] if rucs else None,
        "nombre_cliente":      _nombre_cliente(texto),
        "ruc_cliente":         rucs[1] if len(rucs) > 1 else None,
        "numero_cuenta":       _numero_cuenta(texto),
        "direccion":           _direccion(texto),
        "categoria":           _categoria(texto),
        "periodo":             _periodo(texto),
        "mes":                 extraer_mes(texto),
        "anio":                extraer_anio(texto),
        "fecha_emision":       normalizar_fecha(texto),
        "fecha_vencimiento":   extraer_fecha_vencimiento(texto),
        "consumo_m3":          _consumo_m3(texto),
        "rango_consumo":       _rango_consumo(texto),
        "cargo_fijo":          extraer_monto(texto, ["CARGO FIJO", "Cargo Fijo",
                                                      "CUOTA FIJA"]),
        "agua_potable":        extraer_monto(texto, ["AGUA POTABLE", "Agua Potable",
                                                      "CARGO POR AGUA"]),
        "alcantarillado":      extraer_monto(texto, ["ALCANTARILLADO", "Alcantarillado",
                                                      "DESAGÜE", "DESAGUE"]),
        "tratamiento_aguas":   extraer_monto(texto, ["TRATAMIENTO", "PLANTA DE TRATAMIENTO"]),
        "subtotal":            extraer_monto(texto, ["SUBTOTAL", "Sub Total"]),
        "igv":                 extraer_monto(texto, ["IGV", "I.G.V", "I.G.V."]),
        "deuda_anterior":      extraer_monto(texto, ["DEUDA ANTERIOR", "SALDO ANTERIOR",
                                                      "DEUDA VENCIDA", "ADEUDO ANTERIOR"]),
        "total_pagar":         extraer_monto(texto, ["TOTAL A PAGAR", "Total a Pagar",
                                                      "IMPORTE TOTAL", "MONTO A PAGAR"]),
        # compatibilidad con frontend
        "serie":               _numero_cuenta(texto),
        "numero_doc":          _numero_cuenta(texto),
        "total":               extraer_monto(texto, ["TOTAL A PAGAR", "IMPORTE TOTAL", "TOTAL"]),
    }


def _empresa(texto: str) -> str | None:
    texto_upper = texto.upper()
    for emp in EMPRESAS_AGUA:
        if emp in texto_upper:
            return emp
    # Patrón genérico: "EPS XXXX"
    m = re.search(r"\bEPS\s+([A-Z\s]+)\b", texto_upper)
    return m.group(0).strip() if m else None


def _nombre_cliente(texto: str) -> str | None:
    m = re.search(
        r"(?:Cliente|Titular|Propietario|Beneficiario|Nombre)\s*[:\-]?\s*([A-ZÁÉÍÓÚÑ][^\n]{3,60})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _numero_cuenta(texto: str) -> str | None:
    m = re.search(
        r"N[°º\.ro]*\s*(?:DE\s+)?(?:CUENTA|CODIGO\s+CLIENTE|COD\.?\s*CLIENTE)\s*[:\-]?\s*(\d{5,12})",
        texto, re.IGNORECASE
    )
    if m:
        return m.group(1)
    # SEDAPAL usa código de 10 dígitos
    m = re.search(r"\b(\d{10})\b", texto)
    return m.group(1) if m else None


def _direccion(texto: str) -> str | None:
    m = re.search(
        r"(?:Direcci[oó]n|Domicilio|Predio|Inmueble|Ubicaci[oó]n)\s*[:\-]?\s*([^\n]{5,80})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _categoria(texto: str) -> str | None:
    m = re.search(
        r"(?:Categor[íi]a|Uso|Tipo\s+de\s+Uso)\s*[:\-]?\s*([^\n]{2,30})",
        texto, re.IGNORECASE
    )
    return m.group(1).strip() if m else None


def _periodo(texto: str) -> str | None:
    m = re.search(r"(?:Periodo|Per[íi]odo)\s*[:\-]?\s*([A-Z]+\s*\d{4})", texto, re.IGNORECASE)
    return m.group(1).strip() if m else None




def _consumo_m3(texto: str) -> float | None:
    m = re.search(r"(?:CONSUMO|VOLUMEN)\s*[:\-]?\s*(\d+(?:\.\d+)?)\s*m[3³]", texto, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    # "23 m3" o "m3 23"
    m = re.search(r"(\d+)\s*m[3³]\b", texto, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return None


def _rango_consumo(texto: str) -> str | None:
    m = re.search(r"(?:Rango|Bloque)\s*[:\-]?\s*(\d+\s*[-–a]\s*\d+\s*m[3³]?)", texto, re.IGNORECASE)
    return m.group(1).strip() if m else None
