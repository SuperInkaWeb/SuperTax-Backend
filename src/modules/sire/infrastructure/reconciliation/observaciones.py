"""
Observaciones del Registro de Ventas: correlatividad y duplicados.

Dos revisiones que no cubren los escenarios A/B/C/D:
  - Correlatividad: huecos en la numeración de las notas de crédito (una serie
    debería ser continua; un número faltante puede ser un documento sin declarar).
  - Duplicados: la misma clave tipo+serie+número declarada más de una vez.

Las boletas (tipo 03) se declaran por rangos (número inicial→final); su
correlatividad requiere ese número final, que el parser aún no expone, así que
aquí se cubren los comprobantes de numeración individual (NC). Ver la memoria
`sire-boletas-dashboard-requerimientos`.
"""
import re
from collections import defaultdict
from dataclasses import dataclass

# Tipos con numeración individual sobre los que tiene sentido revisar huecos.
# Las boletas (03) van por rangos y quedan fuera hasta capturar el número final.
_TIPOS_CORRELATIVIDAD = {"07"}

# Si en una serie faltan más de esto, casi seguro no es una secuencia propia
# continua (p. ej. facturas salteadas); no se reporta para no meter ruido.
_MAX_FALTANTES = 500


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


def _compactar(numeros: list[int]) -> str:
    """[721, 722, 723, 730] → '721-723, 730'."""
    partes: list[str] = []
    inicio = previo = numeros[0]
    for n in numeros[1:]:
        if n == previo + 1:
            previo = n
        else:
            partes.append(str(inicio) if inicio == previo else f"{inicio}-{previo}")
            inicio = previo = n
    partes.append(str(inicio) if inicio == previo else f"{inicio}-{previo}")
    return ", ".join(partes)


def detectar_correlatividad(records) -> list[SerieFaltantes]:
    """Por cada serie de NC, los números que faltan entre el mínimo y el máximo."""
    por_serie: dict[tuple[str, str], set[int]] = defaultdict(set)
    for r in records:
        tipo = str(r.tipo_cdp).strip()
        if tipo not in _TIPOS_CORRELATIVIDAD:
            continue
        n = _a_entero(r.numero)
        if n is not None:
            por_serie[(tipo, str(r.serie).strip().upper())].add(n)

    resultado: list[SerieFaltantes] = []
    for (tipo, serie), numeros in por_serie.items():
        if len(numeros) < 2:
            continue
        faltan = sorted(set(range(min(numeros), max(numeros) + 1)) - numeros)
        if not faltan or len(faltan) > _MAX_FALTANTES:
            continue
        resultado.append(SerieFaltantes(tipo, serie, _compactar(faltan), len(faltan)))
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
