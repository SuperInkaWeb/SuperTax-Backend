"""
Modelo del filtro (árbol recursivo con grupos anidados).

Un filtro es un ÁRBOL de nodos:
  - FilterLeaf  = una condición sobre una columna (hoja).
  - FilterGroup = un conector (AND/OR) con hijos, que pueden ser hojas u otros grupos.

Permite lógica anidada como "(region = Lima Y monto > 1000) O tipo = 08". El conjunto
de operadores es un enum CERRADO: cualquiera fuera de la lista se rechaza antes de tocar
la BD (defensa contra inyección, A03). Vive en `domain` porque lo comparten la API
(cuerpos de petición) y el query-builder (infraestructura), sin acoplarse entre sí.
"""
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class Operator(str, Enum):
    EQ = "eq"
    NE = "ne"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    CONTAINS = "contains"
    NOT_CONTAINS = "not_contains"
    STARTS_WITH = "starts_with"
    ENDS_WITH = "ends_with"
    IN = "in"
    NOT_IN = "not_in"
    IS_NULL = "is_null"
    IS_NOT_NULL = "is_not_null"
    BETWEEN = "between"


class Combinator(str, Enum):
    AND = "and"
    OR = "or"


class SortDirection(str, Enum):
    ASC = "asc"
    DESC = "desc"


FilterValue = str | int | float | bool | list[str | int | float] | None


class FilterLeaf(BaseModel):
    """Hoja: una condición sobre una columna."""
    type: Literal["condition"] = "condition"
    column: str
    operator: Operator
    # value: escalar para la mayoría; lista para IN/NOT_IN/BETWEEN; ausente para IS_NULL.
    value: FilterValue = None


class FilterGroup(BaseModel):
    """Grupo: un conector (AND/OR) con hijos (hojas u otros grupos)."""
    type: Literal["group"] = "group"
    combinator: Combinator = Combinator.AND
    children: list["FilterNode"] = Field(default_factory=list)


# Unión discriminada por el campo "type"; recursiva vía FilterGroup.children.
FilterNode = Annotated[Union[FilterLeaf, FilterGroup], Field(discriminator="type")]
FilterGroup.model_rebuild()


class SortSpec(BaseModel):
    column: str
    direction: SortDirection = SortDirection.ASC


class Delimiter(str, Enum):
    """Separadores permitidos para la descarga en TXT (conjunto CERRADO)."""
    TAB = "tab"
    PIPE = "pipe"
    SEMICOLON = "semicolon"
    COMMA = "comma"

    @property
    def char(self) -> str:
        return {
            Delimiter.TAB: "\t",
            Delimiter.PIPE: "|",
            Delimiter.SEMICOLON: ";",
            Delimiter.COMMA: ",",
        }[self]
