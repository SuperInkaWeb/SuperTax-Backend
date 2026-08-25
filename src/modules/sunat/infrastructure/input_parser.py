"""
Parser flexible de la entrada de SUNAT (el Excel/CSV con la lista de comprobantes
a descargar).

A diferencia del lector rígido anterior (columnas con nombres exactos + openpyxl),
este acepta cualquier layout: columnas desordenadas, sin cabecera, con filas de
título, distintos delimitadores/codificaciones y Excel de sistemas contables que
openpyxl no abre. Autocontenido en el módulo SUNAT — no depende de otros módulos.

Estrategia (mapea 4 campos: RUC emisor, tipo, serie, número):
  1. Normaliza Excel → texto delimitado (calamine, tolerante).
  2. Detecta encoding, delimitador y dónde empiezan los datos (salta títulos).
  3. Mapea columnas por cabecera (alias) y completa lo faltante con heurísticas.
  4. Extrae los comprobantes en el formato canónico que consume la automatización.
"""
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from src.modules.sunat.infrastructure.automation.selectores import (
    COLUMNAS_REQUERIDAS,
    TIPO_CP_MAP,
)

# ── Patrones de los 4 campos ─────────────────────────────────────────────────
_RE_RUC = re.compile(r"^(10|15|16|17|20)\d{9}$")   # RUC peruano (11 dígitos)
_RE_DNI = re.compile(r"^\d{8}$")                    # DNI (boletas)
_RE_SERIE = re.compile(r"^[A-Za-z0-9]{1,4}$")       # F001, E001, B001, 0001
_RE_SERIE_LETRA = re.compile(r"^[A-Za-z][A-Za-z0-9]{2,3}$")  # F001, EB01
_RE_ENTERO = re.compile(r"^\d{1,10}$")

_TIPOS_VALIDOS = set(TIPO_CP_MAP)  # {1, 2, 3, 7, 8, 20, 40}

# Texto → código, para entradas donde el tipo viene como palabra (no como número).
_TIPO_TEXTO_A_NUM = {
    "factura": 1,
    "recibo por honorarios": 2,
    "recibo": 2,
    "boleta": 3,
    "boleta de venta": 3,
    "nota de credito": 7,
    "nota de crédito": 7,
    "nota de debito": 8,
    "nota de débito": 8,
    "comprobante de retencion": 20,
    "comprobante de retención": 20,
    "retencion": 20,
    "liquidacion de compra": 40,
    "liquidación de compra": 40,
}

_ALIAS = {
    "ruc": ["nro doc identidad", "nrodocidentidad", "ruc", "ruc emisor", "rucemisor",
            "documento", "nro documento", "identidad", "doc identidad"],
    "tipo": ["tipo cp/doc.", "tipo cp/doc", "tipo cp", "tipocp", "tipo comprobante",
             "tipocomprobante", "tipo doc", "tipo", "cod tipo"],
    "serie": ["serie del cdp", "seriedelcdp", "serie comprobante", "serie", "serie cdp",
              "nro serie", "num serie"],
    "numero": ["nro cp o doc. nro inicial (rango)", "nro cp o doc", "nro cp", "nrocp",
               "numero comprobante", "nro comprobante", "nrocomprobante", "numero",
               "número", "correlativo", "nro", "num", "number"],
}

_NULOS = {"", "nan", "none", "<na>", "null"}

_XLSX_SIG = b"PK\x03\x04"
_XLS_SIG = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


@dataclass
class MapeoEntrada:
    """Cómo interpretar el archivo de entrada de SUNAT."""
    delimiter: str = "|"
    encoding: str = "utf-8"
    has_header: bool = True
    skip_rows: int = 0
    col_ruc: int | None = None
    col_tipo: int | None = None
    col_serie: int | None = None
    col_numero: int | None = None
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        """La automatización necesita los 4 campos para buscar en SUNAT."""
        return None not in (self.col_ruc, self.col_tipo, self.col_serie, self.col_numero)


@dataclass(slots=True)
class ComprobanteEntrada:
    """Comprobante ya normalizado al formato que consume la automatización."""
    id: str
    ruc: str
    serie: str
    numero: int
    tipo_num: int
    tipo_texto: str


# ── Normalización Excel → texto ──────────────────────────────────────────────
def _es_excel(content: bytes) -> bool:
    return content[:4] == _XLSX_SIG or content[:8] == _XLS_SIG


def _fmt_celda(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if pd.isna(v):
            return ""
        return str(int(v)) if v.is_integer() else repr(v)
    if isinstance(v, (datetime, date, pd.Timestamp)):
        return pd.Timestamp(v).strftime("%d/%m/%Y")
    s = re.sub(r"_x[0-9A-Fa-f]{4}_", " ", str(v))
    return " ".join(s.split())


def normalizar_content(content: bytes) -> bytes:
    """Excel (.xlsx/.xls/.xlsb) → texto delimitado por '|'. Idempotente para CSV/TXT."""
    if not _es_excel(content):
        return content
    try:
        df = pd.read_excel(io.BytesIO(content), header=None, dtype=object, engine="calamine")
    except Exception as exc:
        raise ValueError(f"No se pudo leer el Excel: {exc}")
    return df.map(_fmt_celda).to_csv(sep="|", index=False, header=False).encode("utf-8")


def _detectar_encoding(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _detectar_delimitador(lines: list[str]) -> str:
    sample = [ln for ln in lines[:20] if ln.strip()]
    mejor, mejor_score = ",", 0.0
    for delim in ("|", ";", ",", "\t"):
        counts = [ln.count(delim) for ln in sample]
        if not counts or max(counts) == 0:
            continue
        media = sum(counts) / len(counts)
        var = sum((c - media) ** 2 for c in counts) / len(counts)
        score = media / (1 + var)
        if media >= 1 and score > mejor_score:
            mejor, mejor_score = delim, score
    return mejor


# ── Scoring de columnas por contenido ────────────────────────────────────────
def _limpias(valores: list) -> list[str]:
    return [s for s in (str(v).strip() for v in valores) if s.lower() not in _NULOS]


def _score_ruc(valores: list) -> float:
    vals = _limpias(valores)
    if not vals:
        return 0.0
    rucs = sum(1 for v in vals if _RE_RUC.match(v))
    dnis = sum(1 for v in vals if _RE_DNI.match(v))
    return (rucs + 0.5 * dnis) / len(vals)


def _score_serie(valores: list) -> float:
    vals = _limpias(valores)
    if not vals:
        return 0.0
    ok = 0
    for v in vals:
        if _RE_SERIE_LETRA.match(v):
            ok += 1
        elif _RE_SERIE.match(v) and not _RE_RUC.match(v):
            ok += 0.6
    return ok / len(vals)


def _score_numero(valores: list) -> float:
    vals = _limpias(valores)
    if not vals:
        return 0.0
    ok = 0
    for v in vals:
        if _RE_ENTERO.match(v) and not _RE_RUC.match(v) and not _RE_DNI.match(v):
            ok += 1
    return ok / len(vals)


def _score_tipo(valores: list) -> float:
    vals = _limpias(valores)
    if not vals:
        return 0.0
    ok = 0
    for v in vals:
        vl = v.lower()
        if v.isdigit() and int(v) in _TIPOS_VALIDOS:
            ok += 1
        elif any(t in vl for t in _TIPO_TEXTO_A_NUM):
            ok += 1
    return ok / len(vals)


_SCORERS = {
    "ruc": _score_ruc,
    "tipo": _score_tipo,
    "serie": _score_serie,
    "numero": _score_numero,
}


# ── Detección de región y cabecera ───────────────────────────────────────────
def _norm_header(texto: str) -> str:
    return re.sub(r"[\s_]+", " ", str(texto).strip().lower())


def _fila_es_header(celdas: list[str]) -> int:
    """Cuántos de los 4 campos reconoce esta fila como cabecera (por alias)."""
    norms = [_norm_header(c) for c in celdas]
    aciertos = 0
    for aliases in _ALIAS.values():
        if any(any(a == n or a in n for a in aliases) for n in norms):
            aciertos += 1
    return aciertos


def _detectar_inicio(rows: list[list[str]]) -> tuple[int, bool]:
    """Devuelve (skip_rows, has_header). Salta filas de título y ubica la cabecera."""
    mejor_i, mejor_aciertos = -1, 1  # exige ≥2 campos reconocidos para ser cabecera
    for i, celdas in enumerate(rows[:15]):
        aciertos = _fila_es_header(celdas)
        if aciertos > mejor_aciertos:
            mejor_i, mejor_aciertos = i, aciertos
    if mejor_i >= 0:
        return mejor_i, True
    # Sin cabecera clara: primera fila con ≥3 celdas no vacías = inicio de datos.
    for i, celdas in enumerate(rows[:15]):
        if sum(1 for c in celdas if c.strip()) >= 3:
            return i, False
    return 0, False


def _mapear_por_header(headers: list[str]) -> dict[str, int | None]:
    norms = [_norm_header(h) for h in headers]
    res: dict[str, int | None] = {k: None for k in _ALIAS}
    for campo, aliases in _ALIAS.items():
        for i, col in enumerate(norms):  # coincidencia exacta primero
            if col in aliases:
                res[campo] = i
                break
        if res[campo] is None:
            for i, col in enumerate(norms):  # coincidencia parcial
                if any(a in col for a in aliases):
                    res[campo] = i
                    break
    return res


def _completar_por_contenido(
    df: pd.DataFrame, mapeo: dict[str, int | None]
) -> dict[str, int | None]:
    """Asigna por scoring las columnas que la cabecera no resolvió, sin repetir."""
    usadas = {c for c in mapeo.values() if c is not None}
    for campo in ("ruc", "tipo", "serie", "numero"):
        if mapeo[campo] is not None:
            continue
        scorer = _SCORERS[campo]
        mejor_col, mejor = None, 0.35  # umbral mínimo de confianza
        for col in df.columns:
            if col in usadas:
                continue
            score = scorer(df[col].tolist())
            if score > mejor:
                mejor_col, mejor = col, score
        if mejor_col is not None:
            mapeo[campo] = mejor_col
            usadas.add(mejor_col)
    return mapeo


def _leer_df(content: bytes, mapeo: MapeoEntrada, nrows: int | None = None) -> pd.DataFrame:
    df = pd.read_csv(
        io.BytesIO(content),
        sep=re.escape(mapeo.delimiter),
        engine="python",
        header=None,
        skiprows=mapeo.skip_rows + (1 if mapeo.has_header else 0),
        nrows=nrows,
        dtype=str,
        keep_default_na=False,
        encoding=mapeo.encoding,
        on_bad_lines="skip",
    )
    return df


# ── API pública ──────────────────────────────────────────────────────────────
def analizar(content: bytes, mapeo_manual: dict | None = None) -> dict:
    """Analiza el archivo y propone el mapeo. Devuelve mapeo + cabeceras + muestra.

    `mapeo_manual` (opcional) fuerza columnas elegidas por el usuario (índices).
    """
    content = normalizar_content(content)
    encoding = _detectar_encoding(content)
    texto = content.decode(encoding, errors="replace")
    lines = texto.splitlines()
    if not lines:
        return {"mapeo": MapeoEntrada(warnings=["El archivo está vacío"]), "headers": [],
                "muestra": [], "confianza": 0.0, "necesita_revision": True}

    delimiter = _detectar_delimitador(lines)
    rows = [ln.split(delimiter) for ln in lines if ln.strip()]
    skip_rows, has_header = _detectar_inicio(rows)

    headers: list[str] = []
    if has_header and skip_rows < len(rows):
        headers = [c.strip() for c in rows[skip_rows]]

    mapeo = MapeoEntrada(
        delimiter=delimiter, encoding=encoding, has_header=has_header, skip_rows=skip_rows
    )
    df = _leer_df(content, mapeo)

    asignacion: dict[str, int | None] = {k: None for k in _ALIAS}
    if headers:
        asignacion = _mapear_por_header(headers)
    asignacion = _completar_por_contenido(df, asignacion)

    if mapeo_manual:  # el usuario manda: sobrescribe lo detectado
        for campo in _ALIAS:
            if campo in mapeo_manual:
                asignacion[campo] = mapeo_manual[campo]

    mapeo.col_ruc = asignacion["ruc"]
    mapeo.col_tipo = asignacion["tipo"]
    mapeo.col_serie = asignacion["serie"]
    mapeo.col_numero = asignacion["numero"]
    mapeo.confidence = _confianza(df, mapeo)
    if not mapeo.is_usable:
        faltan = [c for c in ("ruc", "tipo", "serie", "numero") if asignacion[c] is None]
        mapeo.warnings.append(f"No se identificaron las columnas: {', '.join(faltan)}")

    muestra = df.head(8).values.tolist()
    return {
        "mapeo": mapeo,
        "headers": headers,
        "muestra": muestra,
        "confianza": mapeo.confidence,
        "necesita_revision": not mapeo.is_usable or mapeo.confidence < 0.6,
    }


def _confianza(df: pd.DataFrame, mapeo: MapeoEntrada) -> float:
    if not mapeo.is_usable or df.empty:
        return 0.0
    scores = [
        _score_ruc(df[mapeo.col_ruc].tolist()),
        _score_tipo(df[mapeo.col_tipo].tolist()),
        _score_serie(df[mapeo.col_serie].tolist()),
        _score_numero(df[mapeo.col_numero].tolist()),
    ]
    return round(sum(scores) / len(scores), 3)


def _a_tipo_num(valor: str) -> int | None:
    v = valor.strip()
    if v.isdigit():
        n = int(v)
        return n if n in _TIPOS_VALIDOS else None
    vl = v.lower()
    for texto, num in _TIPO_TEXTO_A_NUM.items():
        if texto in vl:
            return num
    return None


def extraer_comprobantes(content: bytes, mapeo: MapeoEntrada) -> list[ComprobanteEntrada]:
    """Extrae los comprobantes válidos según el mapeo. Descarta filas incompletas.

    El mapeo (encoding/delimitador) se detectó sobre el contenido ya normalizado a
    texto, así que se normaliza aquí también (idempotente) — de lo contrario un
    Excel binario se leería como UTF-8 y reventaría.
    """
    if not mapeo.is_usable:
        raise ValueError("El mapeo no identifica las 4 columnas requeridas")
    df = _leer_df(normalizar_content(content), mapeo)
    comprobantes: list[ComprobanteEntrada] = []
    for _, fila in df.iterrows():
        serie = str(fila.get(mapeo.col_serie, "")).strip().upper()
        numero_raw = re.sub(r"\D", "", str(fila.get(mapeo.col_numero, "")))
        ruc = re.sub(r"\D", "", str(fila.get(mapeo.col_ruc, "")))
        tipo_num = _a_tipo_num(str(fila.get(mapeo.col_tipo, "")))
        if not serie or not numero_raw or tipo_num is None:
            continue
        numero = int(numero_raw)
        comprobantes.append(ComprobanteEntrada(
            id=f"{serie}-{numero}",
            ruc=ruc,
            serie=serie,
            numero=numero,
            tipo_num=tipo_num,
            tipo_texto=TIPO_CP_MAP[tipo_num],
        ))
    return comprobantes


def a_excel_canonico(comprobantes: list[ComprobanteEntrada]) -> bytes:
    """Escribe un .xlsx con las columnas exactas que espera la automatización.

    Así `automatizar()` sigue leyendo su formato de siempre y no se toca."""
    df = pd.DataFrame(
        [
            {
                "Nro Doc Identidad": c.ruc,
                "Tipo CP/Doc.": c.tipo_num,
                "Serie del CDP": c.serie,
                "Nro CP o Doc. Nro Inicial (Rango)": c.numero,
            }
            for c in comprobantes
        ],
        columns=COLUMNAS_REQUERIDAS,
    )
    buf = io.BytesIO()
    df.to_excel(buf, index=False, engine="openpyxl")
    return buf.getvalue()
