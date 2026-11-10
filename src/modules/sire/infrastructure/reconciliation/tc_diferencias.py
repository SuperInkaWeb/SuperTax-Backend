"""
Comparación de tipo de cambio (requerimiento 2): TC del PLE de ventas vs TC venta
oficial por fecha de emisión. Excepción NC/ND (tipo 07/08): el TC correcto es el
de la fecha del DOCUMENTO MODIFICADO (campo 27 del PLE 14.1), no el de la nota.

Módulo AISLADO: parsea por su cuenta solo las columnas que necesita del PLE 14.1
de ventas (pipe-delimitado), sin tocar el parser ni el motor portados. Para
archivos que no sean PLE 14.1 (p. ej. Excel o mapeos personalizados) no produce
diferencias — la comparación aplica al Registro de Ventas electrónico.

El TC oficial llega resuelto en `tc_venta_por_fecha` ({fecha ISO: venta}), que el
proceso principal arma desde la tabla `sire.tipo_cambio` (poblada del BCRP); este
módulo corre en el subproceso de conciliación, que no tiene acceso a la BD.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from src.modules.sire.infrastructure.reconciliation.engine import TIPO_LABELS

# Índices 0-based del PLE 14.1 de ventas (36 campos, separador '|').
_IDX = {
    "periodo": 0, "cuo": 1, "fecha_emi": 3, "tipo": 5, "serie": 6, "numero": 7,
    "razon": 11, "igv": 15, "total": 24, "moneda": 25, "tc": 26, "fecha_doc_mod": 27,
}
_MIN_CAMPOS = 36
_TIPOS_NOTA = {"07", "08"}  # NC / ND: comparan contra el TC del doc modificado
_MONEDA_LOCAL = "PEN"


@dataclass(slots=True)
class DiferenciaTC:
    """Un comprobante cuyo TC declarado en el PLE no coincide con el oficial."""

    cuo: str
    periodo: str
    fecha_emi: date | None
    fecha_doc_mod: date | None
    tipo_label: str
    num_documento: str
    razon_social: str
    v_igv: float
    v_total: float
    tc_ple: float
    tc_sunat: float
    dif_igv: float


def _decodificar(contenido: bytes) -> str:
    try:
        return contenido.decode("utf-8")
    except UnicodeDecodeError:
        return contenido.decode("latin-1")  # encoding habitual de los PLE de SUNAT


def _parse_fecha(valor: str) -> date | None:
    try:
        parsed = datetime.strptime(valor.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None
    return parsed if parsed.year >= 1900 else None  # '01/01/0001' = fecha nula del PLE


def _parse_decimal(valor: str) -> Decimal | None:
    try:
        return Decimal(valor.strip())
    except (InvalidOperation, AttributeError):
        return None


def comparar_tipo_cambio(
    contenido: bytes, tipo_libro: str, tc_venta_por_fecha: dict[str, str]
) -> list[DiferenciaTC]:
    """Diferencias de TC del PLE de ventas vs el TC oficial.

    Solo para ventas con TC oficial disponible; un comprobante sin diferencia (TC
    igual al milésimo) o en soles no se incluye. `tc_venta_por_fecha`: {fecha ISO
    'AAAA-MM-DD': venta como str}.
    """
    if tipo_libro != "ventas" or not tc_venta_por_fecha:
        return []

    diferencias: list[DiferenciaTC] = []
    for linea in _decodificar(contenido).splitlines():
        if not linea.strip():
            continue
        campos = linea.split("|")
        if len(campos) < _MIN_CAMPOS:
            continue

        moneda = campos[_IDX["moneda"]].strip().upper()
        if not moneda or moneda == _MONEDA_LOCAL:
            continue  # soles: no hay TC que comparar
        tc_ple = _parse_decimal(campos[_IDX["tc"]])
        if tc_ple is None or tc_ple == 0:
            continue

        tipo = campos[_IDX["tipo"]].strip()
        fecha_emi = _parse_fecha(campos[_IDX["fecha_emi"]])
        fecha_doc_mod = (
            _parse_fecha(campos[_IDX["fecha_doc_mod"]]) if tipo in _TIPOS_NOTA else None
        )
        # NC/ND: el TC correcto es el de la fecha del doc modificado; si falta, cae
        # a la fecha de emisión de la propia nota.
        fecha_tc = fecha_doc_mod or fecha_emi
        if fecha_tc is None:
            continue
        venta = tc_venta_por_fecha.get(fecha_tc.isoformat())
        if venta is None:
            continue  # sin TC oficial para esa fecha (fuera de cobertura)
        tc_sunat = Decimal(venta)
        if tc_ple == tc_sunat:
            continue  # calza exacto: no es diferencia

        igv = _parse_decimal(campos[_IDX["igv"]]) or Decimal(0)
        total = _parse_decimal(campos[_IDX["total"]]) or Decimal(0)
        dif_igv = igv * (tc_ple - tc_sunat) / tc_ple
        diferencias.append(
            DiferenciaTC(
                cuo=campos[_IDX["cuo"]].strip(),
                periodo=campos[_IDX["periodo"]].strip(),
                fecha_emi=fecha_emi,
                fecha_doc_mod=fecha_doc_mod,
                tipo_label=f"{tipo} - {TIPO_LABELS.get(tipo, '')}".rstrip(" -"),
                num_documento=f"{campos[_IDX['serie']].strip()}-{campos[_IDX['numero']].strip()}",
                razon_social=campos[_IDX["razon"]].strip(),
                v_igv=float(round(igv, 2)),
                v_total=float(round(total, 2)),
                tc_ple=float(tc_ple),
                tc_sunat=float(tc_sunat),
                dif_igv=float(round(dif_igv, 2)),
            )
        )
    return diferencias
