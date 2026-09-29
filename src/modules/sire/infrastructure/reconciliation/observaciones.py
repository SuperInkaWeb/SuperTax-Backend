"""
Observaciones del Registro de Ventas: correlatividad y duplicados.

Dos revisiones que no cubren los escenarios A/B/C/D:
  - Correlatividad: huecos en la numeración de boletas (03) y notas de crédito
    (07); una serie debería ser continua, y un número faltante puede ser un
    documento sin declarar. Se maneja por rangos: cada boleta puede declararse
    como un rango (número inicial→final) y cada NC como un rango de un solo
    número; se buscan los huecos entre rangos consecutivos sin materializar los
    números (hay series con millones).
  - Duplicados: la misma clave tipo+serie+número declarada más de una vez.

Ver la memoria `sire-boletas-dashboard-requerimientos`.
"""
import re
from collections import defaultdict
from dataclasses import dataclass

# Tipos sobre los que se revisan huecos: boletas (03) y notas de crédito (07).
_TIPOS_CORRELATIVIDAD = {"03", "07"}

# Una serie se marca "irregular" (en vez de listar sus faltantes) si tiene un
# salto gigante entre rangos —típico de una numeración que cambia de longitud,
# p. ej. mezcla números de 7 y 8 dígitos— o demasiados faltantes en total. Así no
# se vuelcan miles/millones de números y se distingue de huecos reales.
_GAP_IRREGULAR = 10_000
_MAX_LISTAR = 1_000
_IRREGULAR = "numeración irregular — revisar la serie manualmente"


@dataclass(slots=True)
class SerieFaltantes:
    """Números ausentes en la secuencia de una serie."""
    tipo: str
    serie: str
    faltantes: str   # compactado, p. ej. "721-728, 730"
    cantidad: int


@dataclass(slots=True)
class Duplicado:
    """Una clave tipo+serie+número declarada más de una vez."""
    tipo: str
    serie: str
    numero: str
    veces: int


def _a_entero(numero: str) -> int | None:
    digitos = re.sub(r"\D", "", numero or "")
    return int(digitos) if digitos else None


def _rango(numero: str, numero_final: str) -> tuple[int, int] | None:
    """(número inicial, número final) del comprobante. Una NC o boleta suelta es
    un rango de un solo número; una boleta por rango usa su número final."""
    ini = _a_entero(numero)
    if ini is None:
        return None
    fin = _a_entero(numero_final)
    if fin is None or fin < ini:
        fin = ini
    return (ini, fin)


def _huecos(rangos: list[tuple[int, int]]) -> tuple[list[tuple[int, int]], int, int]:
    """Tramos faltantes entre rangos consecutivos, su total y el salto más grande,
    sin expandir los números (hay series con millones): solo se comparan bordes."""
    rangos = sorted(rangos)
    tramos: list[tuple[int, int]] = []
    total = 0
    max_tramo = 0
    max_fin = rangos[0][1]
    for ini, fin in rangos[1:]:
        if ini > max_fin + 1:
            ancho = ini - 1 - max_fin
            tramos.append((max_fin + 1, ini - 1))
            total += ancho
            max_tramo = max(max_tramo, ancho)
        if fin > max_fin:
            max_fin = fin
    return tramos, total, max_tramo


def _fmt_tramos(tramos: list[tuple[int, int]]) -> str:
    """[(721, 723), (730, 730)] → '721-723, 730'."""
    return ", ".join(str(a) if a == b else f"{a}-{b}" for a, b in tramos)


def detectar_correlatividad(records) -> list[SerieFaltantes]:
    """Por cada serie de boletas/NC, los números faltantes entre sus rangos."""
    por_serie: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for r in records:
        tipo = str(r.tipo_cdp).strip()
        if tipo not in _TIPOS_CORRELATIVIDAD:
            continue
        rango = _rango(r.numero, getattr(r, "numero_final", ""))
        if rango is not None:
            por_serie[(tipo, str(r.serie).strip().upper())].append(rango)

    resultado: list[SerieFaltantes] = []
    for (tipo, serie), rangos in por_serie.items():
        if len(rangos) < 2:
            continue
        tramos, total, max_tramo = _huecos(rangos)
        if total == 0:
            continue
        if max_tramo > _GAP_IRREGULAR or total > _MAX_LISTAR:
            faltantes = _IRREGULAR
        else:
            faltantes = _fmt_tramos(tramos)
        resultado.append(SerieFaltantes(tipo, serie, faltantes, total))
    resultado.sort(key=lambda x: (x.tipo, x.serie))
    return resultado


def detectar_duplicados(records) -> list[Duplicado]:
    """Claves tipo+serie+número declaradas más de una vez en el archivo."""
    cuenta: dict[tuple[str, str, str], int] = defaultdict(int)
    for r in records:
        clave = (
            str(r.tipo_cdp).strip(),
            str(r.serie).strip().upper(),
            str(r.numero).strip().lstrip("0") or "0",
        )
        cuenta[clave] += 1
    duplicados = [
        Duplicado(tipo, serie, numero, veces)
        for (tipo, serie, numero), veces in cuenta.items()
        if veces > 1
    ]
    duplicados.sort(key=lambda d: (-d.veces, d.tipo, d.serie))
    return duplicados
