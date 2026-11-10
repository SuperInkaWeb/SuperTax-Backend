"""
Validaciones entre periodos del Registro de Ventas (reqs 3 y 4):
  - Duplicidad dentro del mes (serie+número repetidos en el propio archivo), con
    el detalle de cada ocurrencia.
  - Duplicidad contra el mes anterior (serie+número del mes actual ya declarados
    en el PLE del mes previo que sube el usuario).
  - Cortes de correlatividad respecto al mes anterior (si la serie no continúa del
    último número del mes previo).

Trabaja sobre las filas ya parseadas por `ple_ventas`; no toca el motor portado.
"""
import re
from collections import Counter
from dataclasses import dataclass

from src.modules.sire.infrastructure.reconciliation.ple_ventas import FilaPLE

# Correlatividad: boletas (03) y notas de crédito (07), igual que el motor.
_TIPOS_CORRELATIVIDAD = {"03", "07"}

# Boletas (03) se excluyen de la duplicidad cruzada: son ventas a "clientes varios"
# de alto volumen y su colisión de numeración entre meses es ruido, no un error.
_TIPOS_EXCLUIDOS_DUP = {"03"}

# Un hueco entre meses mayor a esto es una discontinuidad de numeración (p. ej. una
# serie que reinicia), no documentos faltantes: se marca para revisar a mano en vez
# de listar miles de números. Mismo criterio que la correlatividad dentro del mes.
_HUECO_IRREGULAR = 1_000

Clave = tuple[str, str, str]


@dataclass(slots=True)
class CorteSerie:
    """Hueco de numeración entre el mes anterior y el actual, por serie."""

    tipo: str
    serie: str
    ultimo_anterior: int
    primero_actual: int
    faltantes: str
    cantidad: int


def _clave(fila: FilaPLE) -> Clave:
    return (fila.tipo, fila.serie, fila.numero.lstrip("0") or "0")


def _a_entero(numero: str) -> int | None:
    digitos = re.sub(r"\D", "", numero or "")
    return int(digitos) if digitos else None


def indice_claves(filas: list[FilaPLE]) -> set[Clave]:
    """Conjunto de (tipo, serie, número) de un periodo (p. ej. el mes anterior)."""
    return {_clave(f) for f in filas}


def claves_duplicadas_en_periodo(filas: list[FilaPLE]) -> set[Clave]:
    """Claves (tipo+serie+número) que aparecen más de una vez en el archivo."""
    conteo = Counter(_clave(f) for f in filas)
    return {clave for clave, veces in conteo.items() if veces > 1}


def duplicados_en_periodo(filas: list[FilaPLE]) -> list[FilaPLE]:
    """Todas las ocurrencias cuya clave (tipo+serie+número) se repite en el propio
    archivo, ordenadas por tipo, serie y número (detalle completo)."""
    repetidas = claves_duplicadas_en_periodo(filas)
    dups = [f for f in filas if _clave(f) in repetidas]
    dups.sort(key=lambda f: (f.tipo, f.serie, _a_entero(f.numero) or 0))
    return dups


def duplicados_con_anterior(
    filas_actual: list[FilaPLE],
    claves_anterior: set[Clave],
    claves_en_periodo: set[Clave] = frozenset(),
) -> list[FilaPLE]:
    """Filas del mes actual cuya clave ya estaba declarada en el mes anterior.

    Excluye boletas (03) y las claves ya reportadas como duplicado DENTRO del mes
    (van en su propia hoja, no se repiten aquí) — criterio del entregable."""
    dups = [
        f
        for f in filas_actual
        if f.tipo not in _TIPOS_EXCLUIDOS_DUP
        and _clave(f) in claves_anterior
        and _clave(f) not in claves_en_periodo
    ]
    dups.sort(key=lambda f: (f.tipo, f.serie, _a_entero(f.numero) or 0))
    return dups


def _ultimos_por_serie(filas: list[FilaPLE]) -> dict[tuple[str, str], int]:
    """Mayor número por (tipo, serie); usa el número final del rango si existe."""
    out: dict[tuple[str, str], int] = {}
    for f in filas:
        if f.tipo not in _TIPOS_CORRELATIVIDAD:
            continue
        n = _a_entero(f.numero_final) or _a_entero(f.numero)
        if n is None:
            continue
        clave = (f.tipo, f.serie)
        out[clave] = max(out.get(clave, n), n)
    return out


def _primeros_por_serie(filas: list[FilaPLE]) -> dict[tuple[str, str], int]:
    """Menor número por (tipo, serie)."""
    out: dict[tuple[str, str], int] = {}
    for f in filas:
        if f.tipo not in _TIPOS_CORRELATIVIDAD:
            continue
        n = _a_entero(f.numero)
        if n is None:
            continue
        clave = (f.tipo, f.serie)
        out[clave] = min(out.get(clave, n), n)
    return out


def cortes_correlatividad(
    filas_actual: list[FilaPLE], filas_anterior: list[FilaPLE]
) -> list[CorteSerie]:
    """Por serie de boletas/NC, el hueco entre el último número del mes anterior y
    el primero del mes actual (si el actual no continúa en +1)."""
    primeros = _primeros_por_serie(filas_actual)
    ultimos = _ultimos_por_serie(filas_anterior)

    cortes: list[CorteSerie] = []
    for (tipo, serie), primero in primeros.items():
        ultimo = ultimos.get((tipo, serie))
        if ultimo is None or primero <= ultimo + 1:
            continue  # serie nueva, o continúa sin hueco
        cantidad = primero - 1 - ultimo
        if cantidad > _HUECO_IRREGULAR:
            faltantes = (
                f"numeración irregular (del {ultimo + 1:,} al {primero - 1:,}) — "
                "revisar manualmente"
            )
        elif cantidad == 1:
            faltantes = str(ultimo + 1)
        else:
            faltantes = f"{ultimo + 1}-{primero - 1}"
        cortes.append(CorteSerie(tipo, serie, ultimo, primero, faltantes, cantidad))
    cortes.sort(key=lambda c: (c.tipo, c.serie))
    return cortes
