"""
Nivel 3: mapeo de columnas configurable y confirmado por el usuario.

Flujo:
  1. analizar_archivo() — inspecciona el archivo y propone un mapeo con el
     nivel de confianza según de dónde salió la propuesta:
       "ple"        → estándar SUNAT detectado (posiciones por norma)
       "plataforma" → encabezados de plataforma conocida
       "guardado"   → mapeo confirmado previamente por la empresa
       "sugerido"   → heurística (solo PRE-llenado, el usuario confirma)
       "desconocido"→ sin propuesta, el usuario asigna desde cero
  2. El usuario confirma/ajusta en el frontend.
  3. parse_con_columnas() — parsea con el mapeo confirmado (determinista).
  4. validar_aritmetica() — la red de seguridad: los montos deben cuadrar.
"""

import io
import re
import pandas as pd

from src.modules.sire.infrastructure.parser.empresa_file import (
    EmpresaRecord,
    _detect_encoding,
    _detect_delimiter,
    _detect_header,
    _norm_date_series,
    _to_float_series,
    _normalize_tipo_cdp,
    _KNOWN_FORMAT_REQUIRED,
    _PLE81_IDX,
    _PLE141_IDX,
    es_ple_compras,
    _try_parse_as_ple_ventas,
)

CAMPOS_VENTAS = [
    {"campo": "fecha_emision",  "etiqueta": "Fecha de emisión",     "obligatorio": True},
    {"campo": "tipo_cdp",       "etiqueta": "Tipo de comprobante",  "obligatorio": True},
    {"campo": "serie",          "etiqueta": "Serie",                "obligatorio": True},
    {"campo": "numero",         "etiqueta": "Número",               "obligatorio": True},
    {"campo": "base_imponible", "etiqueta": "Base imponible",       "obligatorio": True},
    {"campo": "igv",            "etiqueta": "IGV",                  "obligatorio": True},
    {"campo": "importe_total",  "etiqueta": "Importe total",        "obligatorio": True},
    {"campo": "mto_exonerado",  "etiqueta": "Exonerado",            "obligatorio": False},
    {"campo": "mto_inafecto",   "etiqueta": "Inafecto",             "obligatorio": False},
    {"campo": "status_description", "etiqueta": "Estado del comprobante", "obligatorio": False},
]

CAMPOS_COMPRAS = [
    {"campo": "fecha_emision",  "etiqueta": "Fecha de emisión",     "obligatorio": True},
    {"campo": "tipo_cdp",       "etiqueta": "Tipo de comprobante",  "obligatorio": True},
    {"campo": "serie",          "etiqueta": "Serie",                "obligatorio": True},
    {"campo": "numero",         "etiqueta": "Número",               "obligatorio": True},
    {"campo": "ruc_proveedor",  "etiqueta": "RUC del proveedor",    "obligatorio": True},
    {"campo": "razon_social",   "etiqueta": "Razón social proveedor", "obligatorio": False},
    {"campo": "base_imponible", "etiqueta": "BI gravada (DG)",      "obligatorio": True},
    {"campo": "igv",            "etiqueta": "IGV (DG)",             "obligatorio": True},
    {"campo": "bi_dgng",        "etiqueta": "BI DGNG",              "obligatorio": False},
    {"campo": "igv_dgng",       "etiqueta": "IGV DGNG",             "obligatorio": False},
    {"campo": "bi_dng",         "etiqueta": "BI DNG",               "obligatorio": False},
    {"campo": "igv_dng",        "etiqueta": "IGV DNG",              "obligatorio": False},
    {"campo": "valor_adq_ng",   "etiqueta": "Adq. no gravadas",     "obligatorio": False},
    {"campo": "importe_total",  "etiqueta": "Importe total",        "obligatorio": True},
    {"campo": "moneda",         "etiqueta": "Moneda",               "obligatorio": False},
    {"campo": "tipo_cambio",    "etiqueta": "Tipo de cambio",       "obligatorio": False},
]

_CAMPOS_TEXTO = {"fecha_emision", "tipo_cdp", "serie", "numero", "ruc_proveedor", "razon_social", "moneda"}

_PLE_A_CAMPO = {
    "fecha_emision": _PLE81_IDX["fecha"],
    "tipo_cdp":      _PLE81_IDX["tipo"],
    "serie":         _PLE81_IDX["serie"],
    "numero":        _PLE81_IDX["numero"],
    "ruc_proveedor": _PLE81_IDX["ruc"],
    "razon_social":  _PLE81_IDX["razon"],
    "base_imponible": _PLE81_IDX["bi_dg"],
    "igv":           _PLE81_IDX["igv_dg"],
    "bi_dgng":       _PLE81_IDX["bi_dgng"],
    "igv_dgng":      _PLE81_IDX["igv_dgng"],
    "bi_dng":        _PLE81_IDX["bi_dng"],
    "igv_dng":       _PLE81_IDX["igv_dng"],
    "valor_adq_ng":  _PLE81_IDX["valor_adq_ng"],
    "importe_total": _PLE81_IDX["total"],
    "moneda":        _PLE81_IDX["moneda"],
    "tipo_cambio":   _PLE81_IDX["tipo_cambio"],
}

_PLATAFORMA_A_CAMPO = {
    "fecha_emision":  "issued date",
    "tipo_cdp":       "type description",
    "numero":         "number",
    "base_imponible": "amount net",
    "igv":            "amount tax",
    "importe_total":  "amount total",
    "mto_exonerado":  "amount exo",
    "mto_inafecto":   "amount ina",
    "status_description": "status description",
}

_ALIAS_HEADER = {
    "fecha_emision":  ["fecha", "emis", "date", "fchemi"],
    "tipo_cdp":       ["tipo", "type", "tdoc", "cod_doc"],
    "serie":          ["serie", "series"],
    "numero":         ["numero", "nro", "num", "correlativ", "number"],
    "ruc_proveedor":  ["ruc", "doc_prov", "nrodoc"],
    "razon_social":   ["razon", "proveedor", "nombre"],
    "base_imponible": ["base", "bi", "valor", "net"],
    "igv":            ["igv", "impuesto", "tax"],
    "importe_total":  ["total", "importe"],
    "mto_exonerado":  ["exoner", "exo"],
    "mto_inafecto":   ["inafect", "ina"],
    "bi_dgng":        ["dgng"],
    "bi_dng":         ["dng"],
    "valor_adq_ng":   ["no gravad", "ng"],
    "moneda":         ["moneda", "currency", "divisa"],
    "tipo_cambio":    ["cambio", "tc", "exchange"],
    "status_description": ["status description", "estado", "status"],
}

_FECHA_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$|^\d{4}-\d{2}-\d{2}$")
_RUC_RE = re.compile(r"^\d{11}$")
_MONTO_RE = re.compile(r"^-?\d+([.,]\d{1,4})?$")
_SERIE_NUM_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]{1,5}-\d{1,12}$")


def campos_de(tipo_libro: str) -> list[dict]:
    return CAMPOS_COMPRAS if tipo_libro == "compras" else CAMPOS_VENTAS


def _leer_df(content: bytes, delimiter: str, encoding: str, skip_rows: int = 0,
             nrows: int | None = None) -> pd.DataFrame | None:
    try:
        text = content.decode(encoding, errors="replace")
        return pd.read_csv(
            io.StringIO(text),
            sep=re.escape(delimiter),
            header=None,
            skiprows=skip_rows,
            dtype=str,
            skip_blank_lines=True,
            on_bad_lines="skip",
            engine="python",
            nrows=nrows,
        )
    except Exception:
        return None


_ISO_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _detectar_fechas(content: bytes, delimiter: str, encoding: str,
                     has_header: bool, idx_fecha: int, skip_rows: int = 0) -> list[str]:
    """
    Devuelve las fechas AAAA-MM-DD distintas de la columna de fecha, leyendo
    SOLO esa columna y en bloques: la memoria queda acotada sin importar el
    tamaño del archivo (no se carga entero en el proceso padre).
    """
    text = content.decode(encoding, errors="replace")
    fechas: set[str] = set()
    try:
        reader = pd.read_csv(
            io.StringIO(text),
            sep=re.escape(delimiter),
            header=None,
            usecols=[idx_fecha],
            skiprows=skip_rows + (1 if has_header else 0),
            dtype=str,
            skip_blank_lines=True,
            on_bad_lines="skip",
            engine="python",
            chunksize=200_000,
        )
        for chunk in reader:
            col = _norm_date_series(chunk.iloc[:, 0].fillna("").astype(str).str.strip())
            fechas.update(f for f in col.unique().tolist() if _ISO_FECHA.match(f))
    except Exception:
        return sorted(fechas)
    return sorted(fechas)


_PLE141_A_CAMPO = {
    "fecha_emision":  _PLE141_IDX["fecha"],
    "tipo_cdp":       _PLE141_IDX["tipo"],
    "serie":          _PLE141_IDX["serie"],
    "numero":         _PLE141_IDX["numero"],
    "base_imponible": _PLE141_IDX["bi"],
    "igv":            _PLE141_IDX["igv"],
    "importe_total":  _PLE141_IDX["total"],
    "mto_exonerado":  _PLE141_IDX["exonerado"],
    "mto_inafecto":   _PLE141_IDX["inafecto"],
}


def _fila_parece_datos(fila: list[str]) -> float:
    """Fracción de celdas no vacías que parecen un dato real (fecha, RUC, monto,
    número o serie-número). Una fila de datos puntúa alto; una de título, bajo."""
    vals = [c for c in fila if c and c not in ("nan", "None")]
    if len(vals) < 3:
        return 0.0
    hits = sum(
        1 for c in vals
        if _FECHA_RE.match(c) or _RUC_RE.match(c) or _MONTO_RE.match(c) or _SERIE_NUM_RE.match(c)
    )
    return hits / len(vals)


def _fila_parece_header(fila: list[str], tipo_libro: str) -> bool:
    """La fila parece un encabezado: varias celdas coinciden con alias de campos."""
    norm = [c.lower().strip() for c in fila if c.strip()]
    if len(norm) < 3:
        return False
    aciertos = 0
    for celda in norm:
        for spec in campos_de(tipo_libro):
            if any(alias in celda for alias in _ALIAS_HEADER.get(spec["campo"], [])):
                aciertos += 1
                break
    return aciertos >= 3


def _detectar_region(df: pd.DataFrame, tipo_libro: str) -> tuple[int, bool]:
    """
    Encuentra dónde empieza la tabla real dentro de un reporte con bloque de
    título/basura arriba. Devuelve (skip_rows, has_header):
      - skip_rows: filas a saltar antes de la tabla (el encabezado, si lo hay,
        queda como primera fila tras el salto).
      - has_header: si esa primera fila es un encabezado.
    Para archivos ya limpios devuelve (0, ...) → no cambia el comportamiento.
    """
    n = len(df)
    if n == 0:
        return 0, False

    def fila(i: int) -> list[str]:
        return df.iloc[i].fillna("").astype(str).str.strip().tolist()

    inicio = None
    for i in range(min(n, 60)):
        if _fila_parece_datos(fila(i)) >= 0.5:
            # Cuenta filas de datos en una ventana amplia: tolera filas en blanco o
            # etiquetas de sección intercaladas (reportes a doble espacio), en vez
            # de exigir filas consecutivas.
            fin = min(i + 8, n)
            datos = sum(1 for j in range(i, fin) if _fila_parece_datos(fila(j)) >= 0.5)
            if datos >= min(3, n - i):
                inicio = i
                break

    if inicio is None:  # sin tabla clara → comportamiento actual (la fila 0 decide)
        return 0, _detect_header(fila(0))
    if inicio > 0 and _fila_parece_header(fila(inicio - 1), tipo_libro):
        return inicio - 1, True
    return inicio, False


def analizar_archivo(content: bytes, tipo_libro: str, saved_config: dict | None = None) -> dict:
    """Inspecciona el archivo y devuelve columnas + mapeo propuesto + validación."""
    encoding = _detect_encoding(content)
    solo_lectura = False
    formato = None
    skip_rows = 0

    if tipo_libro == "ventas" and _try_parse_as_ple_ventas(content) is not None:
        delimiter, has_header = "|", False
        nivel = "ple"
        mapeo = dict(_PLE141_A_CAMPO)
        combinado = False
        solo_lectura = True
        formato = "PLE 14.1 — Registro de Ventas"
    elif tipo_libro == "compras" and es_ple_compras(content):
        delimiter, has_header = "|", False
        nivel = "ple"
        mapeo = dict(_PLE_A_CAMPO)
        combinado = False
        solo_lectura = True
        formato = "PLE 8.1 — Registro de Compras"
    else:
        text = content.decode(encoding, errors="replace")
        lines = [l for l in text.splitlines() if l.strip()]
        if not lines:
            return {"error": "El archivo está vacío"}
        delimiter = _detect_delimiter(lines)

        # Detecta la región de datos: salta el bloque de título/basura y ubica
        # el encabezado real. Para archivos limpios devuelve (0, ...).
        df_region = _leer_df(content, delimiter, encoding, nrows=120)
        if df_region is None or df_region.empty:
            return {"error": "No se pudo leer el archivo con el delimitador detectado"}
        skip_rows, has_header = _detectar_region(df_region, tipo_libro)

        header_fields = (
            df_region.iloc[skip_rows].fillna("").astype(str).str.strip().tolist()
            if has_header and skip_rows < len(df_region)
            else []
        )
        headers_norm = [h.lower().strip() for h in header_fields]

        mapeo: dict[str, int] = {}
        combinado = False
        nivel = "desconocido"

        if tipo_libro == "ventas" and has_header and _KNOWN_FORMAT_REQUIRED.issubset(set(headers_norm)):
            nivel = "plataforma"
            combinado = True
            for campo, header in _PLATAFORMA_A_CAMPO.items():
                if header in headers_norm:
                    mapeo[campo] = headers_norm.index(header)
        elif saved_config and saved_config.get("columnas"):
            nivel = "guardado"
            mapeo = dict(saved_config["columnas"])
            combinado = bool(saved_config.get("serie_numero_combinado"))
            delimiter = saved_config.get("delimiter", delimiter)
            has_header = bool(saved_config.get("has_header", has_header))
            skip_rows = int(saved_config.get("skip_rows", skip_rows))
        else:
            nivel = "sugerido"
            mapeo, combinado = _sugerir(
                content, delimiter, encoding, has_header, headers_norm, tipo_libro, skip_rows
            )
            if not mapeo:
                nivel = "desconocido"

    df = _leer_df(content, delimiter, encoding, skip_rows=skip_rows, nrows=60)
    if df is None or df.empty:
        return {"error": "No se pudo leer el archivo con el delimitador detectado"}

    start = 1 if has_header else 0
    columnas_archivo = []
    for i in range(len(df.columns)):
        vals = df.iloc[start:start + 3, i].fillna("").astype(str).str.strip().tolist()
        header_name = str(df.iloc[0, i]).strip() if has_header and len(df) > 0 else None
        columnas_archivo.append({"idx": i, "header": header_name, "muestras": vals})

    mapeo = {c: i for c, i in mapeo.items() if i is not None and i < len(df.columns)}

    config = {
        "delimiter": delimiter,
        "encoding": encoding,
        "has_header": has_header,
        "skip_rows": skip_rows,
        "serie_numero_combinado": combinado,
        "columnas": mapeo,
    }
    if tipo_libro == "ventas" and nivel == "ple":
        validacion = {
            "ok": True,
            "aritmetica_pct": None,
            "faltantes": [],
            "avisos": [
                "Aritmética verificada contra la norma (total = suma de los 12 componentes). "
                "BI e IGV incorporan sus columnas de descuento automáticamente."
            ],
        }
    else:
        validacion = validar_mapeo(content, config, tipo_libro)

    fechas_detectadas: list[str] = []
    idx_fecha = mapeo.get("fecha_emision")
    if tipo_libro == "ventas" and idx_fecha is not None:
        fechas_detectadas = _detectar_fechas(
            content, delimiter, encoding, has_header, idx_fecha, skip_rows
        )

    return {
        "nivel": nivel,
        "formato": formato,
        "config": config,
        "columnas_archivo": columnas_archivo,
        "campos": campos_de(tipo_libro),
        "validacion": validacion,
        "solo_lectura": solo_lectura,
        "fechas_detectadas": fechas_detectadas,
    }


def _sugerir(content: bytes, delimiter: str, encoding: str, has_header: bool,
             headers_norm: list[str], tipo_libro: str, skip_rows: int = 0) -> tuple[dict[str, int], bool]:
    """Heurística de PRE-llenado. Nunca decide sola: el usuario confirma."""
    mapeo: dict[str, int] = {}
    usadas: set[int] = set()

    if headers_norm:
        for spec in campos_de(tipo_libro):
            campo = spec["campo"]
            for alias in _ALIAS_HEADER.get(campo, []):
                hit = next(
                    (i for i, h in enumerate(headers_norm) if alias in h and i not in usadas),
                    None,
                )
                if hit is not None:
                    mapeo[campo] = hit
                    usadas.add(hit)
                    break

    combinado = False
    df = _leer_df(content, delimiter, encoding, skip_rows=skip_rows, nrows=40)
    if df is not None and not df.empty:
        start = 1 if has_header else 0
        data = df.iloc[start:]
        for i in range(len(df.columns)):
            if i in usadas:
                continue
            vals = [v for v in data.iloc[:, i].fillna("").astype(str).str.strip() if v]
            if not vals:
                continue
            rate = lambda rx: sum(1 for v in vals if rx.match(v)) / len(vals)
            if "fecha_emision" not in mapeo and rate(_FECHA_RE) > 0.9:
                mapeo["fecha_emision"] = i
                usadas.add(i)
            elif tipo_libro == "compras" and "ruc_proveedor" not in mapeo and rate(_RUC_RE) > 0.9:
                mapeo["ruc_proveedor"] = i
                usadas.add(i)
            elif "numero" not in mapeo and rate(_SERIE_NUM_RE) > 0.9:
                mapeo["numero"] = i
                usadas.add(i)
                combinado = True
                mapeo.pop("serie", None)

    return mapeo, combinado


def parse_con_columnas(content: bytes, config: dict, tipo_libro: str,
                       nrows: int | None = None) -> list[EmpresaRecord]:
    """
    Parsea el archivo con un mapeo confirmado (vectorizado).
    nrows: si se pasa, lee solo esas filas (para validar sobre una muestra sin
    cargar el archivo entero en memoria); None = archivo completo.
    """
    df = _leer_df(
        content, config.get("delimiter", "|"), config.get("encoding") or _detect_encoding(content),
        skip_rows=int(config.get("skip_rows", 0)), nrows=nrows,
    )
    if df is None:
        return []
    if config.get("has_header"):
        df = df.iloc[1:].reset_index(drop=True)
    if df.empty:
        return []

    cols: dict[str, int] = {c: int(i) for c, i in (config.get("columnas") or {}).items()}
    n_cols = len(df.columns)

    def txt(campo: str) -> pd.Series:
        i = cols.get(campo)
        if i is None or i >= n_cols:
            return pd.Series("", index=df.index)
        return df.iloc[:, i].fillna("").astype(str).str.strip()

    def num(campo: str, default: float = 0.0) -> pd.Series:
        i = cols.get(campo)
        if i is None or i >= n_cols:
            return pd.Series(default, index=df.index)
        return _to_float_series(df.iloc[:, i])

    numero_s = txt("numero")
    if config.get("serie_numero_combinado"):
        parts = numero_s.str.split("-", n=1, expand=True)
        if parts.shape[1] == 1:
            parts[1] = None
        has_serie = parts[1].notna()
        serie_s = parts[0].str.upper().where(has_serie, "")
        numero_s = parts[1].where(has_serie, parts[0])
    else:
        serie_s = txt("serie").str.upper()

    valid = ~numero_s.fillna("").isin(["", "nan", "None"])
    keep = valid[valid].index

    tipo_s = txt("tipo_cdp")
    tipo_map = {v: _normalize_tipo_cdp(v) for v in tipo_s.unique()}
    tipo_s = tipo_s.map(tipo_map)

    fecha_s = _norm_date_series(txt("fecha_emision"))
    tc_s = num("tipo_cambio", 1.0).replace(0.0, 1.0)

    series = {
        "tipo": tipo_s, "serie": serie_s, "numero": numero_s, "fecha": fecha_s,
        "base": num("base_imponible"), "igv": num("igv"), "total": num("importe_total"),
        "exo": num("mto_exonerado"), "ina": num("mto_inafecto"),
        "ruc": txt("ruc_proveedor"), "razon": txt("razon_social"),
        "bdgng": num("bi_dgng"), "idgng": num("igv_dgng"),
        "bdng": num("bi_dng"), "idng": num("igv_dng"),
        "ang": num("valor_adq_ng"), "mon": txt("moneda"), "tc": tc_s,
        "estado": txt("status_description"),
    }
    z = {k: s.loc[keep].tolist() for k, s in series.items()}

    return [
        EmpresaRecord(
            tipo_cdp=t, serie=s, numero=n, importe_total=tot,
            fecha_emision=f, base_imponible=b, igv=g,
            mto_exonerado=exo, mto_inafecto=ina,
            ruc_proveedor=ruc, razon_social=razon,
            bi_dgng=bdgng, igv_dgng=idgng, bi_dng=bdng, igv_dng=idng,
            valor_adq_ng=ang, moneda=mon, tipo_cambio=tc,
            status_description=est,
        )
        for t, s, n, f, b, g, tot, exo, ina, ruc, razon, bdgng, idgng, bdng, idng, ang, mon, tc, est in zip(
            z["tipo"], z["serie"], z["numero"], z["fecha"], z["base"], z["igv"], z["total"],
            z["exo"], z["ina"], z["ruc"], z["razon"], z["bdgng"], z["idgng"],
            z["bdng"], z["idng"], z["ang"], z["mon"], z["tc"], z["estado"],
        )
    ]


def validar_mapeo(content: bytes, config: dict, tipo_libro: str) -> dict:
    """
    Valida un mapeo contra el archivo:
    - obligatorios asignados
    - chequeo aritmético: componentes ≈ total en la muestra
    Devuelve {ok, aritmetica_pct, faltantes, avisos}.
    """
    cols = config.get("columnas") or {}
    requeridos = [c["campo"] for c in campos_de(tipo_libro) if c["obligatorio"]]
    if config.get("serie_numero_combinado") and "serie" in requeridos:
        requeridos.remove("serie")
    faltantes = [c for c in requeridos if c not in cols]

    avisos: list[str] = []
    pct = None

    montos_ok = all(c in cols for c in ("base_imponible", "igv", "importe_total"))
    if not faltantes and montos_ok:
        recs = parse_con_columnas(content, config, tipo_libro, nrows=5000)[:2000]
        if recs:
            if tipo_libro == "compras":
                aciertos = sum(
                    1 for r in recs
                    if abs((r.base_imponible + r.igv + r.bi_dgng + r.igv_dgng
                            + r.bi_dng + r.igv_dng + r.valor_adq_ng) - r.importe_total) <= 0.5
                )
            else:
                aciertos = sum(
                    1 for r in recs
                    if abs((r.base_imponible + r.igv + r.mto_exonerado + r.mto_inafecto)
                           - r.importe_total) <= 0.5
                )
            pct = round(aciertos / len(recs) * 100, 1)
            if pct < 90:
                avisos.append(
                    f"Los montos solo cuadran en el {pct}% de la muestra — "
                    "revisa las columnas de montos (puede faltar ISC u otros conceptos, "
                    "o hay columnas mal asignadas)."
                )
            fechas_ok = sum(1 for r in recs if re.match(r"^\d{4}-\d{2}-\d{2}$", r.fecha_emision))
            if fechas_ok / len(recs) < 0.9:
                avisos.append("La columna de fecha no parece contener fechas válidas.")
        else:
            avisos.append("No se extrajo ningún registro con este mapeo.")

    return {
        "ok": not faltantes and (pct is None or pct >= 50),
        "aritmetica_pct": pct,
        "faltantes": faltantes,
        "avisos": avisos,
    }
