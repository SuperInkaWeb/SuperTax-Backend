"""
Parser flexible para archivos TXT/CSV de empresa.

El archivo puede llegar en cualquier formato: columnas desordenadas, sin cabecera,
con datos extra, distintos delimitadores, distintas codificaciones.

Estrategia:
  1. Detectar encoding
  2. Detectar delimitador
  3. Detectar si tiene cabecera
  4. Identificar qué columna es cada campo (tipo_cdp, serie, numero, importe)
     - Si la empresa tiene un mapeo guardado en DB → usarlo directamente
     - Si no → auto-detectar con heurísticas y devolver el resultado + confianza
  5. Extraer los registros con el mapeo elegido

Formato conocido (prioridad alta):
  Si el CSV tiene headers: Type description, Number, Issued date,
  Amount net, Amount tax, Amount total → se parsea directamente (vectorizado).
  Solo este formato aporta fecha/base/igv, necesarios para la conciliación.
"""

import re
import io
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


@dataclass
class ColumnMapping:
    delimiter: str = "|"
    encoding: str = "latin-1"
    has_header: bool = False
    skip_rows: int = 0
    col_tipo_cdp: Optional[int] = None
    col_serie: Optional[int] = None
    col_numero: Optional[int] = None
    col_importe_total: Optional[int] = None
    confidence: float = 0.0
    confirmed_by_user: bool = False
    known_format: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return all(c is not None for c in [self.col_tipo_cdp, self.col_serie, self.col_numero, self.col_importe_total])

    @property
    def is_usable(self) -> bool:
        """Mínimo necesario: numero e importe."""
        return self.col_numero is not None and self.col_importe_total is not None


@dataclass(slots=True)
class EmpresaRecord:
    tipo_cdp: str
    serie: str
    numero: str
    importe_total: float
    fecha_emision: str = ""
    base_imponible: float = 0.0
    igv: float = 0.0
    mto_exonerado: float = 0.0
    mto_inafecto: float = 0.0
    status_description: str = ""
    ruc_proveedor: str = ""
    razon_social: str = ""
    bi_dgng: float = 0.0
    igv_dgng: float = 0.0
    bi_dng: float = 0.0
    igv_dng: float = 0.0
    valor_adq_ng: float = 0.0
    moneda: str = ""
    tipo_cambio: float = 1.0

    @property
    def key(self) -> tuple:
        r = str(self.ruc_proveedor).strip()
        t = str(self.tipo_cdp).strip().upper()
        s = str(self.serie).strip().upper()
        n = str(self.numero).strip().lstrip("0") or "0"
        return (r, t, s, n)


_TIPO_CDP_PATTERN = re.compile(r"^(0[0-9]|[1-9][0-9]|[A-Z]{2})$")

_SERIE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{2,3}[0-9]{0,2}$", re.IGNORECASE)

_NUMERO_PATTERN = re.compile(r"^0*[1-9][0-9]{0,9}$")

_IMPORTE_PATTERN = re.compile(r"^\d{1,10}([.,]\d{1,4})?$")

_HEADER_ALIASES = {
    "tipo_cdp":      ["tipo", "tipo_cdp", "tipo_comprobante", "cod_tipo", "codtipo", "type", "tipocomprobante", "cod"],
    "serie":         ["serie", "series", "num_serie", "numeroserie", "nro_serie", "nroserie"],
    "numero":        ["numero", "nro", "num", "number", "correlativo", "nro_comprobante", "num_comprobante", "nrocomprobante"],
    "importe_total": ["importe_total", "totalimporte", "mto_total", "amount_total", "total_amount",
                      "importe", "total", "monto", "valor", "precio", "amount"],
}

_SERIE_NUMERO_COMBINED = re.compile(r"^([A-Z][A-Z0-9]{2,3})-(\d+)$", re.IGNORECASE)

_KNOWN_FORMAT_REQUIRED = {"type description", "number", "issued date", "amount net", "amount tax", "amount total"}

KNOWN_FORMAT_COLUMNS_HELP = (
    "Type description, Number, Issued date, Amount net, Amount tax, Amount total"
)


def _detect_encoding(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1", "cp1252"):
        try:
            raw.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "latin-1"


def _detect_delimiter(lines: list[str]) -> str:
    candidates = ["|", ";", ",", "\t", "  "]
    scores: dict[str, float] = {}

    sample = [l for l in lines[:20] if l.strip()]
    for delim in candidates:
        counts = [l.count(delim) for l in sample]
        if not counts or max(counts) == 0:
            continue
        mean = sum(counts) / len(counts)
        variance = sum((c - mean) ** 2 for c in counts) / len(counts)
        if mean >= 1:
            scores[delim] = mean / (1 + variance)

    if not scores:
        return ","
    return max(scores, key=lambda d: scores[d])


def _detect_skip_rows(lines: list[str], delimiter: str) -> int:
    """Salta filas vacías o con muy pocas columnas al inicio."""
    expected_cols = None
    for i, line in enumerate(lines[:10]):
        parts = line.split(delimiter)
        if expected_cols is None and len(parts) >= 2:
            expected_cols = len(parts)
            return i
    return 0


def _detect_header(first_row: list[str]) -> bool:
    """La primera fila es cabecera si sus valores parecen nombres, no datos."""
    non_numeric = sum(1 for v in first_row if not re.match(r"^\d+([.,]\d+)?$", v.strip()))
    return non_numeric >= len(first_row) * 0.6


def _map_columns_by_header(headers: list[str]) -> dict[str, int | None]:
    result: dict[str, int | None] = {k: None for k in _HEADER_ALIASES}
    normalized = [h.lower().strip().replace(" ", "_").replace("-", "_") for h in headers]

    for field_name, aliases in _HEADER_ALIASES.items():
        for i, col in enumerate(normalized):
            if col in aliases:
                result[field_name] = i
                break
        if result[field_name] is None:
            for i, col in enumerate(normalized):
                if any(alias in col for alias in aliases):
                    result[field_name] = i
                    break
    return result


def _score_column(values: list, pattern: re.Pattern, expected_uniqueness: float = 0.5) -> float:
    """Calcula qué tan bien una columna encaja con un patrón dado."""
    clean = [s for s in (str(v).strip() for v in values) if s and s not in ("nan", "None", "<NA>")]
    if not clean:
        return 0.0
    match_rate = sum(1 for v in clean if pattern.match(v)) / len(clean)
    unique_rate = len(set(clean)) / len(clean)
    return match_rate * (0.5 + 0.5 * min(unique_rate / expected_uniqueness, 1.0))


def _map_columns_by_data(df: pd.DataFrame) -> tuple[dict[str, int | None], float]:
    n_cols = len(df.columns)
    scores: dict[str, dict[int, float]] = {
        "tipo_cdp":      {},
        "serie":         {},
        "numero":        {},
        "importe_total": {},
    }

    for col_idx in range(n_cols):
        vals = df.iloc[:, col_idx].fillna("").astype(str).tolist()
        scores["tipo_cdp"][col_idx] = _score_column(vals, _TIPO_CDP_PATTERN, expected_uniqueness=0.05)
        scores["serie"][col_idx] = _score_column(vals, _SERIE_PATTERN, expected_uniqueness=0.1)
        scores["numero"][col_idx] = _score_column(vals, _NUMERO_PATTERN, expected_uniqueness=0.9)
        scores["importe_total"][col_idx] = _score_column(vals, _IMPORTE_PATTERN, expected_uniqueness=0.8)

    assignment: dict[str, int | None] = {k: None for k in scores}
    used_cols: set[int] = set()

    field_order = ["importe_total", "numero", "serie", "tipo_cdp"]
    for field_name in field_order:
        best_col = max(
            (c for c in range(n_cols) if c not in used_cols),
            key=lambda c: scores[field_name].get(c, 0),
            default=None,
        )
        if best_col is not None and scores[field_name].get(best_col, 0) > 0.3:
            assignment[field_name] = best_col
            used_cols.add(best_col)

    assigned = sum(1 for v in assignment.values() if v is not None)
    avg_score = sum(
        scores[f][assignment[f]] for f in assignment if assignment[f] is not None
    ) / max(assigned, 1)
    confidence = (assigned / 4) * avg_score

    return assignment, round(confidence, 3)


def _to_float(value) -> float:
    """
    Convierte un string a float soportando separadores de miles:
    "1234.56" → 1234.56 | "1,234.56" → 1234.56 | "1.234,56" → 1234.56 | "1234,56" → 1234.56
    El separador decimal es el que aparece último.
    """
    v = str(value).strip() if value is not None else ""
    if not v or v in ("nan", "None"):
        return 0.0
    if v.rfind(",") > v.rfind("."):
        v = v.replace(".", "").replace(",", ".")
    else:
        v = v.replace(",", "")
    try:
        return float(v)
    except ValueError:
        return 0.0


def _to_float_series(s: pd.Series) -> pd.Series:
    """Versión vectorizada de _to_float para el parser del formato conocido."""
    s = s.fillna("").astype(str).str.strip()
    comma_decimal = s.str.rfind(",") > s.str.rfind(".")
    us_style = s.str.replace(",", "", regex=False)
    eu_style = s.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
    normalized = us_style.where(~comma_decimal, eu_style)
    return pd.to_numeric(normalized, errors="coerce").fillna(0.0)


def _norm_date_series(s: pd.Series) -> pd.Series:
    """Normaliza fechas DD/MM/YYYY → YYYY-MM-DD (vectorizado). Otros formatos pasan tal cual."""
    s = s.fillna("").astype(str).str.strip()
    parts = s.str.extract(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
    is_slash = parts[0].notna()
    rebuilt = parts[2] + "-" + parts[1].str.zfill(2) + "-" + parts[0].str.zfill(2)
    return rebuilt.where(is_slash, s)


def _normalize_tipo_cdp(value) -> str:
    """
    Normaliza códigos de tipo CDP a los 2 dígitos SUNAT estándar.
    Ej: "PE03" → "03", "03" → "03", "Boleta" → "03" (best-effort).
    """
    v = str(value).strip().upper()
    if len(v) == 4 and v[:2].isalpha() and v[2:].isdigit():
        return v[2:]
    _TEXTO_MAP = {
        "BOLETA": "03",
        "BOLETA DE VENTA": "03",
        "FACTURA": "01",
        "FACTURA ELECTRONICA": "01",
        "FACTURA ELECTRÓNICA": "01",
        "NOTA DE CREDITO": "07",
        "NOTA DE CRÉDITO": "07",
        "NOTA DE DEBITO": "08",
        "NOTA DE DÉBITO": "08",
        "TICKET": "03",
        "LIQUIDACION DE COMPRA": "04",
        "LIQUIDACIÓN DE COMPRA": "04",
        "GUIA DE REMISION": "09",
        "GUÍA DE REMISIÓN": "09",
    }
    if v in _TEXTO_MAP:
        return _TEXTO_MAP[v]
    for texto, code in _TEXTO_MAP.items():
        if v.startswith(texto):
            return code
    if len(v) == 1 and v.isdigit() and v != "0":
        return v.zfill(2)
    return v


def _try_parse_as_known_format(content: bytes) -> list[EmpresaRecord] | None:
    """
    Intenta parsear el CSV si contiene los headers del sistema de empresa:
    Type description, Number, Issued date, Amount net, Amount tax, Amount total.

    Totalmente vectorizado — maneja cientos de miles de filas en segundos.
    Retorna lista de registros si el formato coincide, None si no.
    """
    encoding = _detect_encoding(content)
    try:
        text = content.decode(encoding, errors="replace")
    except Exception:
        return None

    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        return None

    delimiter = _detect_delimiter(lines)

    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=delimiter,
            header=0,
            dtype=str,
            skip_blank_lines=True,
            on_bad_lines="skip",
            engine="c" if delimiter != "  " else "python",
        )
    except Exception:
        return None

    df.columns = [str(c).lower().strip() for c in df.columns]
    if not _KNOWN_FORMAT_REQUIRED.issubset(set(df.columns)):
        return None

    number_s = df["number"].fillna("").astype(str).str.strip()
    valid = ~number_s.isin(["", "nan", "None"])
    df = df[valid].reset_index(drop=True)
    number_s = number_s[valid].reset_index(drop=True)
    if df.empty:
        return []

    type_s = df["type description"].fillna("").astype(str)
    tipo_map = {v: _normalize_tipo_cdp(v) for v in type_s.unique()}
    tipo_arr = type_s.map(tipo_map)

    parts = number_s.str.split("-", n=1, expand=True)
    if parts.shape[1] == 1:
        parts[1] = None
    has_serie = parts[1].notna()
    serie_arr  = parts[0].str.upper().where(has_serie, "")
    numero_arr = parts[1].where(has_serie, parts[0])

    fecha_arr = _norm_date_series(df["issued date"])
    net_arr   = _to_float_series(df["amount net"])
    tax_arr   = _to_float_series(df["amount tax"])
    total_arr = _to_float_series(df["amount total"])
    exo_arr   = _to_float_series(df["amount exo"]) if "amount exo" in df.columns else pd.Series(0.0, index=df.index)
    ina_arr   = _to_float_series(df["amount ina"]) if "amount ina" in df.columns else pd.Series(0.0, index=df.index)
    status_arr = (
        df["status description"].fillna("").astype(str).str.strip()
        if "status description" in df.columns
        else pd.Series("", index=df.index)
    )

    return [
        EmpresaRecord(
            tipo_cdp      = t,
            serie         = s,
            numero        = n,
            importe_total = tot,
            fecha_emision = f,
            base_imponible= net,
            igv           = tax,
            mto_exonerado = exo,
            mto_inafecto  = ina,
            status_description = st,
        )
        for t, s, n, f, net, tax, tot, exo, ina, st in zip(
            tipo_arr.tolist(), serie_arr.tolist(), numero_arr.tolist(),
            fecha_arr.tolist(), net_arr.tolist(), tax_arr.tolist(), total_arr.tolist(),
            exo_arr.tolist(), ina_arr.tolist(), status_arr.tolist(),
        )
    ]


_PLE81_IDX = {
    "fecha": 3, "tipo": 5, "serie": 6, "numero": 8,
    "ruc": 11, "razon": 12,
    "bi_dg": 13, "igv_dg": 14, "bi_dgng": 15, "igv_dgng": 16,
    "bi_dng": 17, "igv_dng": 18, "valor_adq_ng": 19,
    "isc": 20, "icbper": 21, "otros": 22,
    "total": 23, "moneda": 24, "tipo_cambio": 25,
}

_PLE_PERIODO_RE = re.compile(r"^\d{6}00$")
_PLE_FECHA_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")

PLE81_FORMAT_HELP = (
    "TXT del Registro de Compras electrónico (PLE 8.1): campos separados por '|', "
    "sin encabezados, con el periodo (AAAAMM00) como primer campo"
)

PLE141_FORMAT_HELP = (
    "TXT del Registro de Ventas electrónico (PLE 14.1 v5): campos separados por '|', "
    "sin encabezados, con el periodo (AAAAMM00) como primer campo"
)

_PLE141_IDX = {
    "fecha": 3, "tipo": 5, "serie": 6, "numero": 7,
    "exportacion": 12, "bi": 13, "dscto_bi": 14, "igv": 15, "dscto_igv": 16,
    "exonerado": 17, "inafecto": 18, "isc": 19,
    "bi_ivap": 20, "ivap": 21, "icbper": 22, "otros": 23,
    "total": 24, "moneda": 25, "tipo_cambio": 26,
}


def es_ple_compras(content: bytes) -> bool:
    """Detección rápida por firma estructural del PLE 8.1 (solo la primera línea)."""
    try:
        head = content[:2000].decode(_detect_encoding(content[:2000]), errors="replace")
    except Exception:
        return False
    first = next((l for l in head.splitlines() if l.strip()), "")
    parts = first.split("|")
    return (
        len(parts) >= 26
        and bool(_PLE_PERIODO_RE.match(parts[0].strip()))
        and bool(_PLE_FECHA_RE.match(parts[3].strip()))
    )


def _try_parse_as_ple_compras(content: bytes) -> list[EmpresaRecord] | None:
    """
    Parsea el Registro de Compras electrónico PLE 8.1 (formato normado por SUNAT,
    sin encabezados, mapeo posicional). Incluye chequeo aritmético: si
    total ≠ suma de componentes en la muestra, las posiciones no son las del
    estándar y se rechaza (return None) en vez de conciliar datos corridos.
    """
    if not es_ple_compras(content):
        return None

    encoding = _detect_encoding(content)
    text = content.decode(encoding, errors="replace")

    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep="|",
            header=None,
            dtype=str,
            skip_blank_lines=True,
            on_bad_lines="skip",
        )
    except Exception:
        return None

    if df.empty or len(df.columns) < 26:
        return None

    def col_s(i: int) -> pd.Series:
        return df.iloc[:, i].fillna("").astype(str).str.strip()

    def col_n(i: int) -> pd.Series:
        return pd.to_numeric(
            col_s(i).str.replace(",", "", regex=False), errors="coerce"
        ).fillna(0.0)

    total_s = col_n(_PLE81_IDX["total"])
    suma_s = sum(
        col_n(_PLE81_IDX[c])
        for c in ("bi_dg", "igv_dg", "bi_dgng", "igv_dgng", "bi_dng", "igv_dng",
                  "valor_adq_ng", "isc", "icbper", "otros")
    )
    muestra = min(len(df), 2000)
    cuadran = ((suma_s.iloc[:muestra] - total_s.iloc[:muestra]).abs() <= 0.02).mean()
    if cuadran < 0.9:
        return None

    valid = ~col_s(_PLE81_IDX["numero"]).eq("")
    df = df[valid].reset_index(drop=True)
    if df.empty:
        return []

    fecha_arr = _norm_date_series(col_s(_PLE81_IDX["fecha"]))

    return [
        EmpresaRecord(
            tipo_cdp      = t,
            serie         = s,
            numero        = n,
            importe_total = tot,
            fecha_emision = f,
            base_imponible= bdg,
            igv           = idg,
            ruc_proveedor = ruc,
            razon_social  = razon,
            bi_dgng       = bdgng,
            igv_dgng      = idgng,
            bi_dng        = bdng,
            igv_dng       = idng,
            valor_adq_ng  = ang,
            moneda        = mon,
            tipo_cambio   = tc if tc else 1.0,
        )
        for t, s, n, f, ruc, razon, bdg, idg, bdgng, idgng, bdng, idng, ang, tot, mon, tc in zip(
            col_s(_PLE81_IDX["tipo"]).tolist(),
            col_s(_PLE81_IDX["serie"]).tolist(),
            col_s(_PLE81_IDX["numero"]).tolist(),
            fecha_arr.tolist(),
            col_s(_PLE81_IDX["ruc"]).tolist(),
            col_s(_PLE81_IDX["razon"]).tolist(),
            col_n(_PLE81_IDX["bi_dg"]).tolist(),
            col_n(_PLE81_IDX["igv_dg"]).tolist(),
            col_n(_PLE81_IDX["bi_dgng"]).tolist(),
            col_n(_PLE81_IDX["igv_dgng"]).tolist(),
            col_n(_PLE81_IDX["bi_dng"]).tolist(),
            col_n(_PLE81_IDX["igv_dng"]).tolist(),
            col_n(_PLE81_IDX["valor_adq_ng"]).tolist(),
            col_n(_PLE81_IDX["total"]).tolist(),
            col_s(_PLE81_IDX["moneda"]).tolist(),
            col_n(_PLE81_IDX["tipo_cambio"]).tolist(),
        )
    ]


def _try_parse_as_ple_ventas(content: bytes) -> list[EmpresaRecord] | None:
    """
    Parsea el Registro de Ventas electrónico PLE 14.1 v5 (36 campos, posicional).
    Chequeo aritmético con TODOS los componentes:
    total(25) = exportación+BI+dsctoBI+IGV+dsctoIGV+exonerado+inafecto+ISC+IVAP+ICBPER+otros.
    BI y dscto se pliegan (BI+dscto), igual que hace el parser de la propuesta SIRE.
    """
    if not es_ple_compras(content):
        return None

    encoding = _detect_encoding(content)
    text = content.decode(encoding, errors="replace")

    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep="|",
            header=None,
            dtype=str,
            skip_blank_lines=True,
            on_bad_lines="skip",
        )
    except Exception:
        return None

    if df.empty or len(df.columns) < 27:
        return None

    def col_s(i: int) -> pd.Series:
        return df.iloc[:, i].fillna("").astype(str).str.strip()

    def col_n(i: int) -> pd.Series:
        return pd.to_numeric(
            col_s(i).str.replace(",", "", regex=False), errors="coerce"
        ).fillna(0.0)

    I = _PLE141_IDX
    total_s = col_n(I["total"])
    suma_s = sum(
        col_n(I[c])
        for c in ("exportacion", "bi", "dscto_bi", "igv", "dscto_igv",
                  "exonerado", "inafecto", "isc", "bi_ivap", "ivap", "icbper", "otros")
    )
    muestra = min(len(df), 2000)
    cuadran = ((suma_s.iloc[:muestra] - total_s.iloc[:muestra]).abs() <= 0.02).mean()
    if cuadran < 0.9:
        return None

    valid = ~col_s(I["numero"]).eq("")
    df = df[valid].reset_index(drop=True)
    if df.empty:
        return []

    fecha_arr = _norm_date_series(col_s(I["fecha"]))
    base_arr = (col_n(I["bi"]) + col_n(I["dscto_bi"]))
    igv_arr = (col_n(I["igv"]) + col_n(I["dscto_igv"]))
    tc_arr = col_n(I["tipo_cambio"]).replace(0.0, 1.0)

    return [
        EmpresaRecord(
            tipo_cdp      = t,
            serie         = s,
            numero        = n,
            importe_total = tot,
            fecha_emision = f,
            base_imponible= b,
            igv           = g,
            mto_exonerado = exo,
            mto_inafecto  = ina,
            moneda        = mon,
            tipo_cambio   = tc,
        )
        for t, s, n, f, b, g, exo, ina, tot, mon, tc in zip(
            col_s(I["tipo"]).tolist(),
            col_s(I["serie"]).tolist(),
            col_s(I["numero"]).tolist(),
            fecha_arr.tolist(),
            base_arr.tolist(),
            igv_arr.tolist(),
            col_n(I["exonerado"]).tolist(),
            col_n(I["inafecto"]).tolist(),
            col_n(I["total"]).tolist(),
            col_s(I["moneda"]).tolist(),
            tc_arr.tolist(),
        )
    ]


def detect_mapping(content: bytes, filename: str = "") -> ColumnMapping:
    """
    Analiza el archivo e intenta detectar su estructura automáticamente.
    Devuelve un ColumnMapping con el mejor intento + confianza.
    No falla: siempre devuelve algo, aunque sea con confianza 0.
    """
    mapping = ColumnMapping()
    mapping.encoding = _detect_encoding(content)

    try:
        text = content.decode(mapping.encoding, errors="replace")
    except Exception:
        text = content.decode("latin-1", errors="replace")

    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        mapping.warnings.append("El archivo está vacío")
        return mapping

    mapping.delimiter = _detect_delimiter(lines)
    mapping.skip_rows = _detect_skip_rows(lines, mapping.delimiter)

    try:
        df = pd.read_csv(
            io.StringIO(text),
            sep=re.escape(mapping.delimiter),
            header=None,
            skiprows=mapping.skip_rows,
            dtype=str,
            skip_blank_lines=True,
            on_bad_lines="skip",
            engine="python",
        )
    except Exception as e:
        mapping.warnings.append(f"Error al parsear: {e}")
        return mapping

    if df.empty or len(df.columns) < 2:
        mapping.warnings.append(f"Solo se detectó {len(df.columns)} columna(s) — revisa el delimitador")
        return mapping

    first_row = df.iloc[0].astype(str).tolist()
    mapping.has_header = _detect_header(first_row)

    if mapping.has_header:
        headers = first_row
        df = df.iloc[1:].reset_index(drop=True)
        col_map = _map_columns_by_header(headers)
        if None in col_map.values():
            data_map, _ = _map_columns_by_data(df)
            for field_name, col_idx in data_map.items():
                if col_map[field_name] is None and col_idx is not None:
                    col_map[field_name] = col_idx
        mapping.confidence = 0.9 if all(v is not None for v in col_map.values()) else 0.6
    else:
        col_map, mapping.confidence = _map_columns_by_data(df)

    mapping.col_tipo_cdp = col_map.get("tipo_cdp")
    mapping.col_serie = col_map.get("serie")
    mapping.col_numero = col_map.get("numero")
    mapping.col_importe_total = col_map.get("importe_total")

    if not mapping.is_usable:
        mapping.warnings.append("No se pudieron identificar los campos mínimos (numero, importe). Requiere configuración manual.")

    return mapping


def parse_with_mapping(content: bytes, mapping: ColumnMapping) -> list[EmpresaRecord]:
    """
    Extrae los registros usando un ColumnMapping ya definido (detectado o manual).
    NOTA: este formato no aporta fecha/base/igv — no es apto para la conciliación
    completa, solo para preview/configuración de mapeo.
    """
    text = content.decode(mapping.encoding, errors="replace")

    df = pd.read_csv(
        io.StringIO(text),
        sep=re.escape(mapping.delimiter),
        header=None,
        skiprows=mapping.skip_rows,
        dtype=str,
        skip_blank_lines=True,
        on_bad_lines="skip",
        engine="python",
    )

    df.columns = range(len(df.columns))

    if mapping.has_header:
        df = df.iloc[1:].reset_index(drop=True)

    records = []
    for _, row in df.iterrows():
        def get_col(idx: int | None, default: str = "") -> str:
            if idx is None or idx >= len(row):
                return default
            return str(row.iloc[idx] if hasattr(row, 'iloc') else row[idx]).strip()

        tipo_cdp = get_col(mapping.col_tipo_cdp, "00")
        serie = get_col(mapping.col_serie, "")
        numero = get_col(mapping.col_numero)
        importe_str = get_col(mapping.col_importe_total, "0")

        if not numero or numero in ("nan", "None", ""):
            continue

        tipo_cdp = _normalize_tipo_cdp(tipo_cdp)

        if not serie or serie.lower() in ("nan", "none", ""):
            m = _SERIE_NUMERO_COMBINED.match(numero)
            if m:
                serie, numero = m.group(1).upper(), m.group(2)

        records.append(EmpresaRecord(
            tipo_cdp=tipo_cdp,
            serie=serie,
            numero=numero,
            importe_total=_to_float(importe_str),
        ))

    return records


def parse_empresa_file(
    content: bytes,
    filename: str = "",
    saved_mapping: ColumnMapping | None = None,
    tipo_libro: str = "ventas",
) -> tuple[list[EmpresaRecord], ColumnMapping]:
    """
    Punto de entrada principal. Los formatos aceptados dependen del libro:
    - ventas:  CSV con headers estándar (formato conocido)
    - compras: TXT PLE 8.1 (Registro de Compras electrónico, posicional)
    Detecta y avisa el caso cruzado (archivo de un libro subido al otro).
    Devuelve (registros, mapping_usado). Solo mapping.known_format=True habilita
    la conciliación.
    """
    if tipo_libro == "compras":
        ple_records = _try_parse_as_ple_compras(content)
        if ple_records is not None:
            return ple_records, ColumnMapping(
                confidence=0.99, has_header=False, confirmed_by_user=True, known_format=True,
            )
        rechazo = ColumnMapping(known_format=False)
        if _try_parse_as_known_format(content) is not None:
            rechazo.warnings.append(
                "El archivo tiene el formato del sistema de VENTAS — elegiste el libro de COMPRAS. "
                "Para compras se espera el TXT PLE 8.1."
            )
        elif _try_parse_as_ple_ventas(content) is not None:
            rechazo.warnings.append(
                "El archivo parece un Registro de VENTAS (PLE 14.1) — elegiste el libro de COMPRAS."
            )
        elif es_ple_compras(content):
            rechazo.warnings.append(
                "El archivo parece un PLE pero sus montos no cuadran con el estándar 8.1 "
                "(total ≠ suma de componentes) — posible estructura corrida o versión no soportada."
            )
        return [], rechazo

    known_records = _try_parse_as_known_format(content)
    if known_records is not None:
        known_mapping = ColumnMapping(
            confidence=0.99, has_header=True, confirmed_by_user=True, known_format=True,
        )
        return known_records, known_mapping

    ple_ventas_records = _try_parse_as_ple_ventas(content)
    if ple_ventas_records is not None:
        return ple_ventas_records, ColumnMapping(
            confidence=0.99, has_header=False, confirmed_by_user=True, known_format=True,
        )

    if es_ple_compras(content):
        rechazo = ColumnMapping(known_format=False)
        if _try_parse_as_ple_compras(content) is not None:
            rechazo.warnings.append(
                "El archivo parece un Registro de COMPRAS (PLE 8.1) — elegiste el libro de VENTAS."
            )
        else:
            rechazo.warnings.append(
                "El archivo parece un PLE pero sus montos no cuadran con el estándar 14.1 "
                "(total ≠ suma de componentes) — posible estructura corrida o versión no soportada."
            )
        return [], rechazo

    if saved_mapping and saved_mapping.confirmed_by_user and saved_mapping.is_usable:
        return parse_with_mapping(content, saved_mapping), saved_mapping

    detected = detect_mapping(content, filename)

    if detected.is_usable:
        records = parse_with_mapping(content, detected)
    else:
        records = []

    return records, detected
