from collections import Counter
from dataclasses import dataclass, field
from typing import Optional, Union
from src.modules.sire.infrastructure.parser.empresa_file import EmpresaRecord
from src.modules.sire.infrastructure.parser.sunat_propuesta import SunatRecord

IGV_DIFF_THRESHOLD = 0.10

TIPOS_CONCILIABLES = {"01", "03", "07", "08"}
TIPOS_CONCILIABLES_COMPRAS = {"01", "07", "08", "30", "50", "54"}

TIPO_LABELS = {
    "01": "Factura",
    "03": "Boleta",
    "05": "Boleto de transporte aéreo",
    "07": "Nota de Crédito",
    "08": "Nota de Débito",
    "09": "Guía de Remisión",
    "04": "Liquidación de Compra",
    "14": "Recibo de servicios públicos",
    "50": "DUA - Importación",
}


@dataclass
class DifferenceDetail:
    campo: str
    valor_empresa: Union[str, float]
    valor_sunat: Union[str, float]
    diferencia: Optional[float]


@dataclass
class ScenarioARecord:
    """En CSV pero no en SUNAT (búsqueda en todo el mes)."""
    tipo_cdp: str
    serie: str
    numero: str
    fecha_emision: str
    base_imponible: float
    igv: float
    importe_total: float
    es_alerta_roja: bool
    status_description: str = ""
    ruc_proveedor: str = ""
    razon_social: str = ""


@dataclass
class ScenarioBRecord:
    """En SUNAT (misma fecha que CSV) pero no en CSV."""
    tipo_cdp: str
    serie: str
    numero: str
    fecha_emision: str
    base_imponible_sunat: float
    igv_sunat: float
    importe_total_sunat: float
    es_alerta_roja: bool
    ruc_proveedor: str = ""
    razon_social: str = ""


@dataclass
class ScenarioCRecord:
    """En ambos, con diferencias en fecha o montos (1 a 6 campos)."""
    tipo_cdp: str
    serie: str
    numero: str
    fecha_emision_empresa: str
    fecha_emision_sunat: str
    base_imponible_empresa: float
    base_imponible_sunat: float
    igv_empresa: float
    igv_sunat: float
    importe_total_empresa: float
    importe_total_sunat: float
    mto_exonerado_empresa: float
    mto_exonerado_sunat: float
    mto_inafecto_empresa: float
    mto_inafecto_sunat: float
    diferencias: list[DifferenceDetail]
    es_alerta_roja: bool
    ruc_proveedor: str = ""
    razon_social: str = ""
    # Solo compras "sin SIRE": periodo AAAAMM de la propuesta donde se halló
    # (distinto al periodo conciliado). Vacío = coincidió en el periodo normal.
    periodo_hallazgo: str = ""
    bi_dgng_empresa: float = 0.0
    bi_dgng_sunat: float = 0.0
    igv_dgng_empresa: float = 0.0
    igv_dgng_sunat: float = 0.0
    bi_dng_empresa: float = 0.0
    bi_dng_sunat: float = 0.0
    igv_dng_empresa: float = 0.0
    igv_dng_sunat: float = 0.0
    valor_adq_ng_empresa: float = 0.0
    valor_adq_ng_sunat: float = 0.0
    moneda_empresa: str = ""
    moneda_sunat: str = ""
    tipo_cambio_empresa: float = 0.0
    tipo_cambio_sunat: float = 0.0

    @property
    def campos_diferentes(self) -> set[str]:
        return {d.campo for d in self.diferencias}


@dataclass(slots=True)
class ScenarioDRecord:
    """En ambos, sin diferencias relevantes (los 6 campos coinciden con tolerancia de S/ 0.01)."""
    tipo_cdp: str
    serie: str
    numero: str
    fecha_emision_empresa: str
    fecha_emision_sunat: str
    base_imponible_empresa: float
    base_imponible_sunat: float
    igv_empresa: float
    igv_sunat: float
    importe_total_empresa: float
    importe_total_sunat: float
    mto_exonerado_empresa: float
    mto_exonerado_sunat: float
    mto_inafecto_empresa: float
    mto_inafecto_sunat: float
    ruc_proveedor: str = ""
    razon_social: str = ""
    moneda_empresa: str = ""
    moneda_sunat: str = ""
    tipo_cambio_empresa: float = 0.0
    tipo_cambio_sunat: float = 0.0
    # Solo compras "sin SIRE": periodo AAAAMM de la propuesta donde se halló.
    periodo_hallazgo: str = ""


@dataclass
class ReconciliationOutput:
    scenario_a: list[ScenarioARecord]
    scenario_b: list[ScenarioBRecord]
    scenario_c: list[ScenarioCRecord]
    scenario_d: list[ScenarioDRecord] = field(default_factory=list)
    excluidos_por_tipo: dict[str, int] = field(default_factory=dict)
    csv_duplicados: int = 0
    sunat_duplicados: int = 0

    @property
    def total_excluidos(self) -> int:
        return sum(self.excluidos_por_tipo.values())

    @property
    def igv_diferencia_total(self) -> float:
        total = 0.0
        campos_igv = {"igv", "igv_dgng", "igv_dng"}
        for rec in self.scenario_c:
            for diff in rec.diferencias:
                if diff.campo in campos_igv and diff.diferencia is not None:
                    total += abs(diff.diferencia)
        for rec in self.scenario_b:
            total += abs(rec.igv_sunat)
        return round(total, 2)

    @property
    def tiene_alertas_rojas(self) -> bool:
        return (
            any(r.es_alerta_roja for r in self.scenario_a)
            or any(r.es_alerta_roja for r in self.scenario_b)
            or any(r.es_alerta_roja for r in self.scenario_c)
        )


def _mes_de_fecha(fecha: str) -> str | None:
    """'2026-05-14' → '202605'. None si no es una fecha ISO reconocible."""
    if not fecha or len(fecha) < 7:
        return None
    y, m = fecha[:4], fecha[5:7]
    if y.isdigit() and m.isdigit():
        return y + m
    return None


def _calcular_diferencias(emp: EmpresaRecord, sun: SunatRecord, es_compras: bool) -> list[DifferenceDetail]:
    """Compara un par empresa↔SUNAT y devuelve las diferencias campo por campo."""
    diffs: list[DifferenceDetail] = []

    def _cmp_num(campo: str, v_emp: float, v_sun: float, tol: float = 0.01, nd: int = 2):
        d = round(v_emp - v_sun, nd)
        if abs(d) > tol:
            diffs.append(DifferenceDetail(campo, v_emp, v_sun, d))

    if emp.fecha_emision and sun.fecha_emision and emp.fecha_emision != sun.fecha_emision:
        diffs.append(DifferenceDetail("fecha", emp.fecha_emision, sun.fecha_emision, None))

    _cmp_num("base_imponible", emp.base_imponible, sun.base_imponible)
    _cmp_num("igv", emp.igv, sun.igv)
    _cmp_num("importe_total", emp.importe_total, sun.importe_total)

    if es_compras:
        _cmp_num("bi_dgng", emp.bi_dgng, sun.bi_dgng)
        _cmp_num("igv_dgng", emp.igv_dgng, sun.igv_dgng)
        _cmp_num("bi_dng", emp.bi_dng, sun.bi_dng)
        _cmp_num("igv_dng", emp.igv_dng, sun.igv_dng)
        _cmp_num("valor_adq_ng", emp.valor_adq_ng, sun.valor_adq_ng)
        if emp.moneda and sun.moneda and emp.moneda.strip().upper() != sun.moneda.strip().upper():
            diffs.append(DifferenceDetail("moneda", emp.moneda, sun.moneda, None))
        _cmp_num("tipo_cambio", emp.tipo_cambio, sun.tipo_cambio, tol=0.001, nd=3)
    else:
        _cmp_num("mto_exonerado", emp.mto_exonerado, sun.mto_exonerado)
        _cmp_num("mto_inafecto", emp.mto_inafecto, sun.mto_inafecto)

    return diffs


_CAMPOS_IGV = {"igv", "igv_dgng", "igv_dng"}


def _construir_c(emp: EmpresaRecord, sun: SunatRecord,
                 diffs: list[DifferenceDetail], periodo_hallazgo: str = "") -> ScenarioCRecord:
    es_roja = any(
        d.campo in _CAMPOS_IGV and d.diferencia is not None and abs(d.diferencia) > IGV_DIFF_THRESHOLD
        for d in diffs
    )
    return ScenarioCRecord(
        tipo_cdp               = emp.tipo_cdp,
        serie                  = emp.serie,
        numero                 = emp.numero,
        fecha_emision_empresa  = emp.fecha_emision,
        fecha_emision_sunat    = sun.fecha_emision,
        base_imponible_empresa = emp.base_imponible,
        base_imponible_sunat   = sun.base_imponible,
        igv_empresa            = emp.igv,
        igv_sunat              = sun.igv,
        importe_total_empresa  = emp.importe_total,
        importe_total_sunat    = sun.importe_total,
        mto_exonerado_empresa  = emp.mto_exonerado,
        mto_exonerado_sunat    = sun.mto_exonerado,
        mto_inafecto_empresa   = emp.mto_inafecto,
        mto_inafecto_sunat     = sun.mto_inafecto,
        diferencias            = diffs,
        es_alerta_roja         = es_roja,
        ruc_proveedor          = emp.ruc_proveedor,
        razon_social           = emp.razon_social or sun.razon_social,
        periodo_hallazgo       = periodo_hallazgo,
        bi_dgng_empresa        = emp.bi_dgng,
        bi_dgng_sunat          = sun.bi_dgng,
        igv_dgng_empresa       = emp.igv_dgng,
        igv_dgng_sunat         = sun.igv_dgng,
        bi_dng_empresa         = emp.bi_dng,
        bi_dng_sunat           = sun.bi_dng,
        igv_dng_empresa        = emp.igv_dng,
        igv_dng_sunat          = sun.igv_dng,
        valor_adq_ng_empresa   = emp.valor_adq_ng,
        valor_adq_ng_sunat     = sun.valor_adq_ng,
        moneda_empresa         = emp.moneda,
        moneda_sunat           = sun.moneda,
        tipo_cambio_empresa    = emp.tipo_cambio,
        tipo_cambio_sunat      = sun.tipo_cambio,
    )


def _construir_d(emp: EmpresaRecord, sun: SunatRecord, periodo_hallazgo: str = "") -> ScenarioDRecord:
    return ScenarioDRecord(
        tipo_cdp               = emp.tipo_cdp,
        serie                  = emp.serie,
        numero                 = emp.numero,
        fecha_emision_empresa  = emp.fecha_emision,
        fecha_emision_sunat    = sun.fecha_emision,
        base_imponible_empresa = emp.base_imponible,
        base_imponible_sunat   = sun.base_imponible,
        igv_empresa            = emp.igv,
        igv_sunat              = sun.igv,
        importe_total_empresa  = emp.importe_total,
        importe_total_sunat    = sun.importe_total,
        mto_exonerado_empresa  = emp.mto_exonerado,
        mto_exonerado_sunat    = sun.mto_exonerado,
        mto_inafecto_empresa   = emp.mto_inafecto,
        mto_inafecto_sunat     = sun.mto_inafecto,
        ruc_proveedor          = emp.ruc_proveedor,
        razon_social           = emp.razon_social or sun.razon_social,
        moneda_empresa         = emp.moneda,
        moneda_sunat           = sun.moneda,
        tipo_cambio_empresa    = emp.tipo_cambio,
        tipo_cambio_sunat      = sun.tipo_cambio,
        periodo_hallazgo       = periodo_hallazgo,
    )


def _construir_a(emp: EmpresaRecord) -> ScenarioARecord:
    igv_total = emp.igv + emp.igv_dgng + emp.igv_dng
    return ScenarioARecord(
        tipo_cdp      = emp.tipo_cdp,
        serie         = emp.serie,
        numero        = emp.numero,
        fecha_emision = emp.fecha_emision,
        base_imponible= emp.base_imponible,
        igv           = igv_total,
        importe_total = emp.importe_total,
        es_alerta_roja= abs(igv_total) > IGV_DIFF_THRESHOLD,
        status_description = emp.status_description,
        ruc_proveedor = emp.ruc_proveedor,
        razon_social  = emp.razon_social,
    )


def reconcile(
    empresa_records: list[EmpresaRecord],
    sunat_records: list[SunatRecord],
    tipo_libro: str = "ventas",
    cobertura_fechas: set[str] | None = None,
    sunat_extra: dict[str, list[SunatRecord]] | None = None,
    periodo: str | None = None,
) -> ReconciliationOutput:
    """
    sunat_extra: solo compras "sin SIRE". Mapea periodo AAAAMM → propuesta de ese
    mes. Los comprobantes de la empresa que no están en la propuesta del periodo
    conciliado se buscan en la propuesta del mes de su fecha de emisión: si están
    (y cuadran) pasan a D, si difieren pasan a C, y si tampoco están se quedan en A.
    """
    es_compras = tipo_libro == "compras"
    tipos_ok = TIPOS_CONCILIABLES_COMPRAS if es_compras else TIPOS_CONCILIABLES

    conciliables = [r for r in empresa_records if r.key[1] in tipos_ok]
    excluidos = Counter(r.key[1] for r in empresa_records if r.key[1] not in tipos_ok)

    empresa_index = {r.key: r for r in conciliables}
    sunat_index   = {r.key: r for r in sunat_records}

    csv_duplicados   = len(conciliables) - len(empresa_index)
    sunat_duplicados = len(sunat_records) - len(sunat_index)

    # Índices por mes de las propuestas extra (compras "sin SIRE").
    extra_index: dict[str, dict[tuple, SunatRecord]] = {
        p: {r.key: r for r in recs} for p, recs in (sunat_extra or {}).items()
    }

    if es_compras:
        csv_dates: set[str] = set()
    elif cobertura_fechas is not None:
        csv_dates = set(cobertura_fechas)
    else:
        csv_dates = {r.fecha_emision for r in conciliables if r.fecha_emision}

    scenario_a: list[ScenarioARecord] = []
    scenario_c: list[ScenarioCRecord] = []
    scenario_d: list[ScenarioDRecord] = []

    for key, emp in empresa_index.items():
        if key in sunat_index:
            continue
        # "Sin SIRE": el comprobante emitido en otro mes puede estar en la
        # propuesta de ese mes. Se reclasifica a C/D si aparece allí.
        if extra_index:
            mes = _mes_de_fecha(emp.fecha_emision)
            if mes and mes != periodo and mes in extra_index:
                sun = extra_index[mes].get(key)
                if sun is not None:
                    diffs = _calcular_diferencias(emp, sun, es_compras)
                    if diffs:
                        scenario_c.append(_construir_c(emp, sun, diffs, periodo_hallazgo=mes))
                    else:
                        scenario_d.append(_construir_d(emp, sun, periodo_hallazgo=mes))
                    continue
        scenario_a.append(_construir_a(emp))

    scenario_b: list[ScenarioBRecord] = []
    for key, sun in sunat_index.items():
        if key in empresa_index:
            continue
        if csv_dates and sun.fecha_emision not in csv_dates:
            continue
        igv_total = sun.igv + sun.igv_dgng + sun.igv_dng
        es_roja = abs(igv_total) > IGV_DIFF_THRESHOLD
        scenario_b.append(ScenarioBRecord(
            tipo_cdp            = sun.tipo_cdp,
            serie               = sun.serie,
            numero              = sun.numero,
            fecha_emision       = sun.fecha_emision,
            base_imponible_sunat= sun.base_imponible,
            igv_sunat           = igv_total,
            importe_total_sunat = sun.importe_total,
            es_alerta_roja      = es_roja,
            ruc_proveedor       = sun.ruc_proveedor,
            razon_social        = sun.razon_social,
        ))

    for key in empresa_index.keys() & sunat_index.keys():
        emp = empresa_index[key]
        sun = sunat_index[key]
        diffs = _calcular_diferencias(emp, sun, es_compras)
        if diffs:
            scenario_c.append(_construir_c(emp, sun, diffs))
        else:
            scenario_d.append(_construir_d(emp, sun))

    return ReconciliationOutput(
        scenario_a=scenario_a,
        scenario_b=scenario_b,
        scenario_c=scenario_c,
        scenario_d=scenario_d,
        excluidos_por_tipo=dict(excluidos),
        csv_duplicados=csv_duplicados,
        sunat_duplicados=sunat_duplicados,
    )
