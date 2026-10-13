"""
Vocabulario del análisis que no es un árbol de expresiones ni de filtros: el modo
de coincidencia del reemplazo y la definición de una tabla dinámica (pivote).

Vive en `domain` porque lo comparten la API (cuerpos de petición) y los builders
(infraestructura), sin acoplarse entre sí. Los conjuntos de modos/agregaciones son
enums CERRADOS (defensa A03): cualquiera fuera de la lista se rechaza.
"""
from enum import Enum

from pydantic import BaseModel, Field

from src.modules.datafilter.domain.filtering import FilterGroup, SortDirection


class MatchMode(str, Enum):
    """Cómo se compara el texto a buscar (conjunto CERRADO).

    EXACT    -> reemplaza solo cuando el valor de la celda es idéntico.
    CONTAINS -> reemplaza cada aparición del texto dentro del valor.
    """
    EXACT = "exact"
    CONTAINS = "contains"


class ReplacementRule(BaseModel):
    """Una corrección: en `column`, cambiar `search` por `replace`.

    `case_sensitive` solo aplica al modo EXACT; CONTAINS distingue mayúsculas
    siempre (se documenta en la UI).
    """
    column: str
    mode: MatchMode = MatchMode.EXACT
    search: str = Field(min_length=1, max_length=500)
    replace: str = Field(default="", max_length=500)  # vacío = eliminar el texto
    case_sensitive: bool = True


class Aggregation(str, Enum):
    """Agregaciones permitidas en un pivote (conjunto CERRADO)."""
    COUNT = "count"                    # count(col) o count(*) si no hay columna
    COUNT_DISTINCT = "count_distinct"  # count(DISTINCT col)
    SUM = "sum"
    AVG = "avg"
    MIN = "min"
    MAX = "max"


class Measure(BaseModel):
    """Una métrica del reporte: una agregación sobre una columna.

    `column` solo puede omitirse para COUNT (=> count(*), conteo de filas)."""
    column: str | None = None
    aggregation: Aggregation


class PivotSort(BaseModel):
    """Orden del reporte: por una columna de agrupación o por una métrica.

    Exactamente uno de `column` / `measure_index`. Si ambos son None, se ordena por
    las columnas de agrupación (por defecto)."""
    column: str | None = None
    measure_index: int | None = Field(default=None, ge=0)
    direction: SortDirection = SortDirection.DESC


class PivotSpec(BaseModel):
    """Definición de un pivote (sin paginación ni destino). Base compartida por las
    operaciones de ver, guardar y descargar (DRY)."""
    filter: FilterGroup | None = None   # filtro previo; None = todas las filas
    group_by: list[str] = Field(min_length=1, max_length=6)
    measures: list[Measure] = Field(min_length=1, max_length=10)
    # Cross-tab opcional: los valores distintos de esta columna se vuelven columnas.
    pivot_column: str | None = None
    sort: PivotSort | None = None       # None = por columnas de agrupación
    # Mostrar cada valor como % del total de su métrica (solo Suma/Conteo).
    percent_of_total: bool = False
