"""
Cruce de boletas de venta (tipo 03) por agregación serie + día.

Las boletas se declaran de formas muy distintas según la empresa: sueltas (una
por fila), por rangos de numeración, o como un resumen "CLIENTES VARIOS". SUNAT,
en cambio, siempre las entrega una por una en su propuesta. Cruzarlas por número
—como al resto de comprobantes— produce miles de falsos faltantes.

La solución, independiente del estilo de agrupación, es sumar ambos lados por
(serie normalizada, fecha) y comparar los totales del día: cualquier estilo
colapsa al mismo total diario. Ver la memoria `sire-boletas-dashboard-requerimientos`.
"""
from collections import defaultdict
from dataclasses import dataclass, field

from src.modules.sire.infrastructure.parser.empresa_file import EmpresaRecord
from src.modules.sire.infrastructure.parser.sunat_propuesta import SunatRecord

TIPO_BOLETA = "03"

# Tolerancia de S/ 0.01 al comparar montos e IGV que marca alerta roja: los
# mismos umbrales que usa el motor documento a documento (ver engine.py).
_TOL = 0.01
_IGV_ALERTA = 0.10

# Campos monetarios que se suman y comparan por serie + día (semántica de ventas).
_CAMPOS = ("base", "igv", "importe", "exonerado", "inafecto")

# Estado de una boleta agregada (una serie en un día).
BOLETA_SOLO_EMPRESA = "solo_empresa"   # en tu archivo, no en SUNAT
BOLETA_SOLO_SUNAT = "solo_sunat"       # en SUNAT, no en tu archivo
BOLETA_CUADRA = "cuadra"               # en ambos, totales iguales
BOLETA_DIFIERE = "difiere"             # en ambos, algún total distinto


def es_boleta(record) -> bool:
    """Un comprobante es boleta de venta si su tipo es '03'."""
    return str(record.tipo_cdp).strip() == TIPO_BOLETA


def normalizar_serie(serie: str) -> str:
    """
    Lleva la serie de boletas a una forma común entre el archivo de la empresa y
    la propuesta SUNAT. El cliente a veces escribe la serie como dígitos ('0127')
    y SUNAT como 'B' + 3 ('B127'); esta regla las hace coincidir SIN fusionar
    series que llevan letras propias ('B621' ≠ 'B62I' ≠ 'BM49').

    Regla conservadora (validada con datos reales de varias empresas):
      - solo dígitos → 'B' + los dígitos sin ceros de más, con mínimo 3 cifras
        ('0127' → 'B127', '0006' → 'B006').
      - con letra    → tal cual, en mayúsculas ('B621' → 'B621').
    """
    s = (serie or "").strip().upper()
    if s.isdigit():
        return "B" + str(int(s)).zfill(3)
    return s


@dataclass(slots=True)
class _Totales:
    """Acumulador de montos de una serie en un día."""
    base: float = 0.0
    igv: float = 0.0
    importe: float = 0.0
    exonerado: float = 0.0
    inafecto: float = 0.0


@dataclass(slots=True)
class BoletaComparada:
    """Resultado del cruce de una serie de boletas en un día: totales de cada
    lado, estado y —si difieren— qué montos no coinciden."""
    serie: str
    fecha: str
    base_empresa: float
    igv_empresa: float
    importe_empresa: float
    exonerado_empresa: float
    inafecto_empresa: float
    base_sunat: float
    igv_sunat: float
    importe_sunat: float
    exonerado_sunat: float
    inafecto_sunat: float
    estado: str
    campos_diferentes: list[str] = field(default_factory=list)
    es_alerta_roja: bool = False

    @property
    def diferencia_igv(self) -> float:
        """IGV de tu archivo menos el de SUNAT (positivo = declaraste de más)."""
        return round(self.igv_empresa - self.igv_sunat, 2)


def _agregar(records: list) -> dict[tuple[str, str], _Totales]:
    """Suma los montos de las boletas por (serie normalizada, fecha)."""
    buckets: dict[tuple[str, str], _Totales] = defaultdict(_Totales)
    for r in records:
        clave = (normalizar_serie(r.serie), (r.fecha_emision or "").strip())
        t = buckets[clave]
        t.base += r.base_imponible
        t.igv += r.igv
        t.importe += r.importe_total
        t.exonerado += r.mto_exonerado
        t.inafecto += r.mto_inafecto
    return buckets


def comparar_boletas(
    empresa_boletas: list[EmpresaRecord],
    sunat_boletas: list[SunatRecord],
) -> list[BoletaComparada]:
    """
    Agrega ambos lados por serie + día y los compara. Devuelve una fila por cada
    combinación serie+día presente en cualquiera de los dos, ordenada por fecha
    y serie. No importa cómo agrupe la empresa las boletas: la suma diaria las
    vuelve comparables contra el detalle de SUNAT.
    """
    emp = _agregar(empresa_boletas)
    sun = _agregar(sunat_boletas)

    resultado: list[BoletaComparada] = []
    for clave in emp.keys() | sun.keys():
        serie, fecha = clave
        e = emp.get(clave) or _Totales()
        s = sun.get(clave) or _Totales()

        if clave not in sun:
            estado, campos = BOLETA_SOLO_EMPRESA, []
        elif clave not in emp:
            estado, campos = BOLETA_SOLO_SUNAT, []
        else:
            campos = [c for c in _CAMPOS if abs(getattr(e, c) - getattr(s, c)) > _TOL]
            estado = BOLETA_DIFIERE if campos else BOLETA_CUADRA

        es_roja = estado != BOLETA_CUADRA and abs(round(e.igv - s.igv, 2)) > _IGV_ALERTA
        resultado.append(BoletaComparada(
            serie=serie, fecha=fecha,
            base_empresa=round(e.base, 2), igv_empresa=round(e.igv, 2),
            importe_empresa=round(e.importe, 2), exonerado_empresa=round(e.exonerado, 2),
            inafecto_empresa=round(e.inafecto, 2),
            base_sunat=round(s.base, 2), igv_sunat=round(s.igv, 2),
            importe_sunat=round(s.importe, 2), exonerado_sunat=round(s.exonerado, 2),
            inafecto_sunat=round(s.inafecto, 2),
            estado=estado, campos_diferentes=campos, es_alerta_roja=es_roja,
        ))

    resultado.sort(key=lambda b: (b.fecha, b.serie))
    return resultado
