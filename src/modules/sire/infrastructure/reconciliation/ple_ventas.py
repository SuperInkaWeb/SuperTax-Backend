"""
Parser aislado del PLE 14.1 de ventas (pipe-delimitado, 36 campos), para las
validaciones que no toca el motor portado (tipo de cambio, duplicidad y
correlatividad entre periodos). Extrae solo los campos de interés por índice.

No reemplaza al parser del motor (`parser/empresa_file.py`): aquí se necesitan
campos que aquel no expone (CUO, número final del rango, fecha del doc modificado)
y en una forma de solo lectura por fila.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

# Índices 0-based del PLE 14.1 de ventas.
IDX = {
    "periodo": 0, "cuo": 1, "fecha_emi": 3, "tipo": 5, "serie": 6, "numero": 7,
    "numero_final": 8, "razon": 11, "base_imponible": 13, "igv": 15, "total": 24,
    "moneda": 25, "tc": 26, "fecha_doc_mod": 27,
}
MIN_CAMPOS = 36


@dataclass(slots=True)
class FilaPLE:
    """Una fila del PLE de ventas con los campos usados por las validaciones."""

    periodo: str
    cuo: str
    fecha_emi: date | None
    fecha_doc_mod: date | None
    tipo: str
    serie: str
    numero: str
    numero_final: str
    razon_social: str
    base_imponible: Decimal
    igv: Decimal
    total: Decimal
    moneda: str
    tc: Decimal | None


def decodificar(contenido: bytes) -> str:
    try:
        return contenido.decode("utf-8")
    except UnicodeDecodeError:
        return contenido.decode("latin-1")  # encoding habitual de los PLE de SUNAT


def parse_fecha(valor: str) -> date | None:
    try:
        parsed = datetime.strptime(valor.strip(), "%d/%m/%Y").date()
    except (ValueError, AttributeError):
        return None
    return parsed if parsed.year >= 1900 else None  # '01/01/0001' = fecha nula del PLE


def parse_decimal(valor: str) -> Decimal | None:
    try:
        return Decimal(valor.strip())
    except (InvalidOperation, AttributeError):
        return None


def parse_ple_ventas(contenido: bytes) -> list[FilaPLE]:
    """Convierte el PLE 14.1 de ventas en filas. Ignora líneas con pocos campos
    (p. ej. Excel o mapeos personalizados): las validaciones aplican al PLE."""
    filas: list[FilaPLE] = []
    for linea in decodificar(contenido).splitlines():
        if not linea.strip():
            continue
        c = linea.split("|")
        if len(c) < MIN_CAMPOS:
            continue
        filas.append(
            FilaPLE(
                periodo=c[IDX["periodo"]].strip(),
                cuo=c[IDX["cuo"]].strip(),
                fecha_emi=parse_fecha(c[IDX["fecha_emi"]]),
                fecha_doc_mod=parse_fecha(c[IDX["fecha_doc_mod"]]),
                tipo=c[IDX["tipo"]].strip(),
                serie=c[IDX["serie"]].strip().upper(),
                numero=c[IDX["numero"]].strip(),
                numero_final=c[IDX["numero_final"]].strip(),
                razon_social=c[IDX["razon"]].strip(),
                base_imponible=parse_decimal(c[IDX["base_imponible"]]) or Decimal(0),
                igv=parse_decimal(c[IDX["igv"]]) or Decimal(0),
                total=parse_decimal(c[IDX["total"]]) or Decimal(0),
                moneda=c[IDX["moneda"]].strip().upper(),
                tc=parse_decimal(c[IDX["tc"]]),
            )
        )
    return filas
