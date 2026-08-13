import re

from src.modules.scanner.infrastructure.config import TESSERACT_CMD

EXTENSIONES_IMAGEN = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}

MESES = {
    "ENERO":1,"FEBRERO":2,"MARZO":3,"ABRIL":4,"MAYO":5,"JUNIO":6,
    "JULIO":7,"AGOSTO":8,"SEPTIEMBRE":9,"OCTUBRE":10,"NOVIEMBRE":11,"DICIEMBRE":12,
    "ENE":1,"FEB":2,"MAR":3,"ABR":4,"MAY":5,"JUN":6,
    "JUL":7,"AGO":8,"SEP":9,"OCT":10,"NOV":11,"DIC":12,
}

_MESES_ABR = {
    "ENE":"01","FEB":"02","MAR":"03","ABR":"04","MAY":"05","JUN":"06",
    "JUL":"07","AGO":"08","SEP":"09","OCT":"10","NOV":"11","DIC":"12",
}


def limpiar_texto(texto: str) -> str:
    texto = re.sub(r'[^\S\n]+', ' ', texto)
    texto = re.sub(r'\n{3,}', '\n\n', texto)
    return texto.strip()


def extraer_ruc(texto: str) -> str | None:
    match = re.search(r"\b(?:10|15|17|20)\d{9}\b", texto)
    return match.group(0) if match else None


def extraer_todos_los_ruc(texto: str) -> list:
    return re.findall(r"\b(?:10|15|17|20)\d{9}\b", texto)


def extraer_monto(texto: str, etiquetas: list) -> float | None:
    for etiqueta in etiquetas:
        pattern_sol = rf"\b{re.escape(etiqueta)}\b[^\n]{{0,25}}?S/\s*([\d,]+\.\d{{1,2}})"
        match = re.search(pattern_sol, texto, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                pass
        if etiqueta.upper() in ("TOTAL", "TOTAL:"):
            pattern = rf"(?<!Sub\s)(?<!sub\s)(?<!SUB\s)\b{re.escape(etiqueta)}\b\s*[:\.]?\s*([\d,]+\.\d{{1,2}})"
        else:
            pattern = rf"\b{re.escape(etiqueta)}\b\s*[:\.]?\s*([\d,]+\.\d{{1,2}})"
        match = re.search(pattern, texto, re.IGNORECASE)
        if match:
            try:
                return float(match.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def normalizar_fecha(texto: str) -> str | None:
    contextos = [
        r"(?:Emisi[oó]n|Emitid[ao]|Fecha[:\s]+)\s*[:\s]*(\d{2}[/\-]\d{2}[/\-]\d{2,4})",
        r"(\d{2}[/\-]\d{2}[/\-]\d{4})",
        r"(\d{4}[/\-]\d{2}[/\-]\d{2})",
        r"(\d{2}[/\-]\d{2}[/\-]\d{2})",
    ]
    for patron in contextos:
        match = re.search(patron, texto, re.IGNORECASE)
        if match:
            raw = match.group(1) if match.lastindex else match.group(0)
            resultado = _parsear_fecha(raw)
            if resultado:
                return resultado
    return None


def _parsear_fecha(raw: str) -> str | None:
    if not re.search(r'[/\-]', raw):
        return None
    partes = re.split(r'[/\-]', raw)
    if len(partes) != 3:
        return None
    p0, p1, p2 = partes
    if len(p0) == 4:
        return f"{p0}-{p1}-{p2}"
    anio = p2 if len(p2) == 4 else f"20{p2}"
    return f"{anio}-{p1}-{p0}"


def normalizar_hora(texto: str) -> str | None:
    match = re.search(r"\b(\d{1,2}):(\d{2})(?::(\d{2}))?\b", texto)
    if match:
        h, m, s = match.group(1), match.group(2), match.group(3) or "00"
        return f"{int(h):02d}:{m}:{s}"
    return None


def extraer_serie_numero(texto: str) -> tuple:
    match = re.search(r'\b([A-Z]\d{3})\s*[-–]\s*(\d{5,9})\b', texto)
    if match:
        return match.group(1), match.group(2)
    match = re.search(r"\b([A-Z]{1,3}\d*[-–]\d+)\b", texto)
    if match:
        partes = re.split(r"[-–]", match.group(0), maxsplit=1)
        return partes[0], partes[1] if len(partes) > 1 else None
    match2 = re.search(
        r"[Ss]erie[:\s]+([A-Z0-9]+).*?[Nn][uúu]m(?:ero)?[:\s]+(\d+)", texto, re.DOTALL
    )
    if match2:
        return match2.group(1), match2.group(2)
    return None, None


def extraer_mes(texto: str) -> int | None:
    m = re.search(
        r"\b(ENERO|FEBRERO|MARZO|ABRIL|MAYO|JUNIO|JULIO|AGOSTO|SEPTIEMBRE|OCTUBRE|NOVIEMBRE|DICIEMBRE"
        r"|ENE|FEB|MAR|ABR|MAY|JUN|JUL|AGO|SEP|OCT|NOV|DIC)\b",
        texto.upper()
    )
    return MESES.get(m.group(1)) if m else None


def extraer_anio(texto: str) -> int | None:
    m = re.search(r"\b(20\d{2})\b", texto)
    return int(m.group(1)) if m else None


def parsear_fecha_con_mes_abreviado(texto: str) -> str | None:
    m = re.match(r"(\d{2})/([A-Z]{3})/(\d{4})", texto.upper())
    if m:
        mes = _MESES_ABR.get(m.group(2))
        if mes:
            return f"{m.group(3)}-{mes}-{m.group(1)}"
    return None


def extraer_fecha_vencimiento(texto: str) -> str | None:
    m = re.search(
        r"(?:VENCIMIENTO|VENCE|FECHA\s+VENC\.?|PAGAR\s+ANTES\s+DEL?)\s*[:\-]?\s*"
        r"(\d{2}[/\-]\d{2}[/\-]\d{4}|\d{2}/[A-Z]{3}/\d{4})",
        texto, re.IGNORECASE
    )
    if m:
        return parsear_fecha_con_mes_abreviado(m.group(1)) or normalizar_fecha(m.group(1))
    return None
