"""
Vocabulario del análisis que no es un árbol de expresiones ni de filtros:
el modo de coincidencia y la regla de un buscar-y-reemplazar por columna.

Vive en `domain` porque lo comparten la API (cuerpos de petición) y el
replace-builder (infraestructura), sin acoplarse entre sí. El conjunto de modos
es un enum CERRADO (defensa A03): cualquiera fuera de la lista se rechaza.
"""
from enum import Enum

from pydantic import BaseModel, Field


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
