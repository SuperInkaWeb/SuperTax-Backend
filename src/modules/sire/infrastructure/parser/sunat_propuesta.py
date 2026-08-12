import io
import re
from dataclasses import dataclass

import pandas as pd


@dataclass(slots=True)
class SunatRecord:
    tipo_cdp: str
    serie: str
    numero: str
    fecha_emision: str
    base_imponible: float
    igv: float
    importe_total: float
    tipo_cambio: float
    mto_exonerado: float = 0.0
    mto_inafecto: float = 0.0
    ruc_proveedor: str = ""
    razon_social: str = ""
    bi_dgng: float = 0.0
    igv_dgng: float = 0.0
    bi_dng: float = 0.0
    igv_dng: float = 0.0
    valor_adq_ng: float = 0.0
    moneda: str = ""

    @property
    def key(self) -> tuple:
        r = str(self.ruc_proveedor).strip()
        t = str(self.tipo_cdp).strip().upper()
        s = str(self.serie).strip().upper()
        n = str(self.numero).strip().lstrip("0") or "0"
        return (r, t, s, n)


VENTAS_COL_IDX: dict[str, int] = {
    "fecha_emision":           4,
    "tipo_cdp":                6,
    "serie":                   7,
    "numero":                  8,
    "base_imponible":          14,
    "descuento_base_imponible":15,
    "igv":                     16,
    "descuento_igv":           17,
    "mto_exonerado":           18,
    "mto_inafecto":            19,
    "importe_total":           25,
    "tipo_cambio":             27,
}

COMPRAS_COL_IDX: dict[str, int] = {
    "fecha_emision":  4,
    "tipo_cdp":       6,
    "serie":          7,
    "numero":         9,
    "ruc_proveedor":  12,
    "razon_social":   13,
    "base_imponible": 14,
    "igv":            15,
    "bi_dgng":        16,
    "igv_dgng":       17,
    "bi_dng":         18,
    "igv_dng":        19,
    "valor_adq_ng":   20,
    "importe_total":  24,
    "moneda":         25,
    "tipo_cambio":    26,
}


def _col(df: pd.DataFrame, idx: int) -> pd.Series:
    """Devuelve la columna idx como Series de strings limpios."""
    if idx >= len(df.columns):
        return pd.Series("", index=df.index, dtype=str)
    return df.iloc[:, idx].fillna("").astype(str).str.strip()


def _to_float(series: pd.Series) -> pd.Series:
    """Convierte una Series de strings a float, coma → punto, errores → 0."""
    return pd.to_numeric(series.str.replace(",", ".", regex=False), errors="coerce").fillna(0.0)


_FECHA_SLASH = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")


def _normalizar_fecha(valor: str) -> str:
    """dd/mm/yyyy → yyyy-mm-dd; cualquier otro formato se devuelve tal cual."""
    m = _FECHA_SLASH.match(valor)
    if not m:
        return valor
    d, mo, y = m.groups()
    return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"


def parse_sunat_propuesta(txt_bytes: bytes, tipo_libro: str) -> list[SunatRecord]:
    """
    Parsea el TXT pipe-delimited de la propuesta SUNAT.
    tipo_libro: 'compras' | 'ventas'

    Procesa por chunks de 1M de filas: el pico de RAM queda acotado por el
    tamaño de un chunk más los registros acumulados, no por el archivo entero.
    """
    encoding = "latin-1"
    for enc in ("utf-8-sig", "utf-8"):
        try:
            txt_bytes[:65536].decode(enc)
            encoding = enc
            break
        except UnicodeDecodeError:
            continue

    col_idx = COMPRAS_COL_IDX if tipo_libro == "compras" else VENTAS_COL_IDX

    read_opts = dict(
        sep="|", header=0, dtype=str, skip_blank_lines=True,
        on_bad_lines="skip", encoding=encoding, encoding_errors="replace",
        chunksize=1_000_000,
    )
    usecols = sorted(set(col_idx.values()))
    try:
        reader = pd.read_csv(io.BytesIO(txt_bytes), usecols=usecols, **read_opts)
        chunk = next(reader, None)
        pos = {orig: i for i, orig in enumerate(usecols)}
    except ValueError:
        reader = pd.read_csv(io.BytesIO(txt_bytes), **read_opts)
        chunk = next(reader, None)
        pos = {orig: orig for orig in col_idx.values()}
    del txt_bytes

    records: list[SunatRecord] = []
    fecha_map: dict[str, str] = {}
    while chunk is not None:
        df = chunk
        chunk = None
        _procesar_chunk(df, col_idx, pos, fecha_map, records)
        del df
        chunk = next(reader, None)
    return records


def _procesar_chunk(
    df: pd.DataFrame,
    col_idx: dict[str, int],
    pos: dict[int, int],
    fecha_map: dict[str, str],
    records: list[SunatRecord],
) -> None:
    """Convierte un chunk del TXT en SunatRecords y los agrega a `records`."""
    if df.empty:
        return

    def _serie(campo: str) -> pd.Series:
        if campo not in col_idx:
            return pd.Series("", index=df.index, dtype=str)
        return _col(df, pos[col_idx[campo]])

    def _num(campo: str) -> pd.Series:
        if campo not in col_idx:
            return pd.Series(0.0, index=df.index)
        return _to_float(_col(df, pos[col_idx[campo]]))

    tipo_cdp_s = _serie("tipo_cdp")
    serie_s    = _serie("serie")
    numero_s   = _serie("numero")
    fecha_s    = _serie("fecha_emision")
    base_s     = _num("base_imponible")
    igv_s      = _num("igv")
    importe_s  = _num("importe_total")
    tc_s       = _num("tipo_cambio").replace(0.0, 1.0)

    if "descuento_base_imponible" in col_idx:
        base_s = base_s + _num("descuento_base_imponible")
    if "descuento_igv" in col_idx:
        igv_s = igv_s + _num("descuento_igv")

    exo_s     = _num("mto_exonerado")
    ina_s     = _num("mto_inafecto")
    ruc_s     = _serie("ruc_proveedor")
    razon_s   = _serie("razon_social")
    moneda_s  = _serie("moneda")
    dgng_b_s  = _num("bi_dgng")
    dgng_i_s  = _num("igv_dgng")
    dng_b_s   = _num("bi_dng")
    dng_i_s   = _num("igv_dng")
    ang_s     = _num("valor_adq_ng")

    mask = ~(tipo_cdp_s.eq("") & serie_s.eq("") & numero_s.eq(""))
    if not mask.all():
        (tipo_cdp_s, serie_s, numero_s, fecha_s, base_s, igv_s, importe_s, tc_s,
         exo_s, ina_s, ruc_s, razon_s, moneda_s,
         dgng_b_s, dgng_i_s, dng_b_s, dng_i_s, ang_s) = (
            s[mask].reset_index(drop=True)
            for s in (tipo_cdp_s, serie_s, numero_s, fecha_s, base_s, igv_s, importe_s, tc_s,
                      exo_s, ina_s, ruc_s, razon_s, moneda_s,
                      dgng_b_s, dgng_i_s, dng_b_s, dng_i_s, ang_s)
        )

    for v in fecha_s.unique():
        if v not in fecha_map:
            fecha_map[v] = _normalizar_fecha(v)
    fecha_norm = fecha_s.map(fecha_map)
    del fecha_s

    records.extend(
        SunatRecord(
            tipo_cdp       = t,
            serie          = s,
            numero         = n,
            fecha_emision  = f,
            base_imponible = b,
            igv            = g,
            importe_total  = m,
            tipo_cambio    = tc,
            mto_exonerado  = exo,
            mto_inafecto   = ina,
            ruc_proveedor  = ruc,
            razon_social   = razon,
            bi_dgng        = dgb,
            igv_dgng       = dgi,
            bi_dng         = dnb,
            igv_dng        = dni,
            valor_adq_ng   = ang,
            moneda         = mon,
        )
        for t, s, n, f, b, g, m, tc, exo, ina, ruc, razon, dgb, dgi, dnb, dni, ang, mon in zip(
            tipo_cdp_s.tolist(), serie_s.tolist(), numero_s.tolist(), fecha_norm.tolist(),
            base_s.tolist(), igv_s.tolist(), importe_s.tolist(), tc_s.tolist(),
            exo_s.tolist(), ina_s.tolist(),
            ruc_s.tolist(), razon_s.tolist(),
            dgng_b_s.tolist(), dgng_i_s.tolist(), dng_b_s.tolist(), dng_i_s.tolist(),
            ang_s.tolist(), moneda_s.tolist(),
        )
    )
