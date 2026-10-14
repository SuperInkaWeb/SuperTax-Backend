"""
Entidades y políticas del dominio de extracción (puro, sin I/O).

La confianza se guarda POR CAMPO, no por documento: un registro puede tener el RUC
perfectamente legible y el importe borroso. Con una sola cifra global habría que
descartar el documento entero o aceptarlo con un dato dudoso; por campo, la cola
de revisión puede señalar exactamente qué mirar.

Los valores de los enums coinciden con los que persiste
`infrastructure.models` (Completeness/ReviewStatus y la columna `estrategia_usada`),
de modo que el mapeo a BD es directo sin tablas de traducción.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

# Umbral por debajo del cual un campo se considera dudoso y arrastra el registro a
# revisión humana. 0.80 sale de la práctica: el texto nativo de un PDF ronda 1.0,
# el OCR sobre un documento limpio 0.85-0.95, y por debajo de 0.80 los errores de
# lectura dejan de ser anecdóticos.
UMBRAL_DE_CONFIANZA: Final = 0.80


class Estrategia(StrEnum):
    """Motores de lectura, en orden de costo creciente."""

    TEXTO_NATIVO = "native_pdf_text"
    TABLAS_PDF = "pdf_tables"
    OCR_LOCAL = "local_ocr"
    VISION_IA = "vision_ai"


class Completitud(StrEnum):
    """Calidad global del resultado de una extracción (= Completeness en el modelo)."""

    VACIO = "empty"
    PARCIAL = "partial"
    COMPLETO = "complete"


class EstadoDeRevision(StrEnum):
    """Estado de revisión humana (= ReviewStatus en el modelo)."""

    NO_REQUERIDA = "not_required"
    PENDIENTE = "pending"
    APROBADO = "approved"
    RECHAZADO = "rejected"


@dataclass(frozen=True, slots=True)
class CampoExtraido:
    """Un valor leído del documento, con su confianza y su procedencia."""

    valor: str
    confianza: float
    estrategia: Estrategia

    def __post_init__(self) -> None:
        if not 0.0 <= self.confianza <= 1.0:
            raise ValueError(f"Confianza fuera de rango: {self.confianza}")

    @property
    def es_fiable(self) -> bool:
        return self.confianza >= UMBRAL_DE_CONFIANZA


@dataclass(slots=True)
class ResultadoDeEstrategia:
    """
    Lo que devuelve un motor de lectura: campos crudos con su confianza.

    Crudos a propósito: la estrategia lee texto, no interpreta dominio. La conversión
    a objetos de valor la hace el agregador en un único sitio, de modo que todas las
    estrategias se benefician de la misma interpretación.
    """

    estrategia: Estrategia
    campos: dict[str, CampoExtraido] = field(default_factory=dict)
    texto_crudo: str = ""
    duracion_ms: int = 0
    error: str | None = None

    @property
    def tuvo_exito(self) -> bool:
        return self.error is None and bool(self.campos)


# ── Políticas de calidad ─────────────────────────────────────────────

# Campos sin los cuales el registro no sirve: identifican al contribuyente, el
# periodo y el dinero. Si falta alguno, no hay fila que llevar al reporte.
CAMPOS_IMPRESCINDIBLES: Final[frozenset[str]] = frozenset(
    {"ruc_contribuyente", "periodo", "importe_pagado"}
)

# Campos que enriquecen el registro pero cuya ausencia no lo invalida.
CAMPOS_DESEABLES: Final[frozenset[str]] = frozenset(
    {
        "nombre_contribuyente", "ruc_inquilino", "nombre_inquilino", "tipo_doc_inquilino",
        "tipo_de_bien", "fecha_de_pago", "numero_de_operacion", "monto_alquiler",
        "tributo_resultante", "intereses_moratorios",
    }
)


def es_suficiente_para_detenerse(resultado: ResultadoDeEstrategia) -> bool:
    """
    ¿Vale la pena seguir probando estrategias más caras? Se corta solo si TODOS los
    campos imprescindibles están presentes y por encima del umbral. Conformarse con
    la confianza media dejaría pasar un resultado con el RUC perfecto y el importe
    ilegible, que es justo el caso en que una estrategia mejor aporta algo.
    """
    for nombre in CAMPOS_IMPRESCINDIBLES:
        campo = resultado.campos.get(nombre)
        if campo is None or not campo.es_fiable:
            return False
    return True


def combinar(resultados: list[ResultadoDeEstrategia]) -> dict[str, CampoExtraido]:
    """
    Toma el mejor valor de cada campo entre todas las estrategias. Campo a campo y no
    "la mejor estrategia completa": es habitual que el texto nativo lea el RUC sin
    error y falle en un importe dentro de una imagen, donde el OCR sí acierta. Ante un
    empate exacto gana el primero (la estrategia más barata, por el orden del pipeline).
    """
    mejores: dict[str, CampoExtraido] = {}
    for resultado in resultados:
        if not resultado.tuvo_exito:
            continue
        for nombre, campo in resultado.campos.items():
            actual = mejores.get(nombre)
            if actual is None or campo.confianza > actual.confianza:
                mejores[nombre] = campo
    return mejores


def clasificar(campos: dict[str, CampoExtraido]) -> Completitud:
    """Califica el resultado global a partir de los campos reunidos."""
    presentes = {n for n, c in campos.items() if c.valor.strip()}

    if not presentes:
        return Completitud.VACIO
    if not CAMPOS_IMPRESCINDIBLES.issubset(presentes):
        return Completitud.PARCIAL

    hay_dudoso = any(not campos[nombre].es_fiable for nombre in CAMPOS_IMPRESCINDIBLES)
    return Completitud.PARCIAL if hay_dudoso else Completitud.COMPLETO


def decidir_revision(
    completitud: Completitud, campos: dict[str, CampoExtraido]
) -> EstadoDeRevision:
    """
    ¿Necesita este registro que lo mire una persona? Un registro vacío no entra en la
    cola: no hay nada que corregir y llenar la cola de esos casos la vuelve inútil.
    Un completo con algún campo deseable dudoso también se revisa: es barato de
    corregir y evita que un nombre mal leído llegue al reporte con apariencia de bueno.
    """
    if completitud is Completitud.VACIO:
        return EstadoDeRevision.NO_REQUERIDA
    if completitud is Completitud.PARCIAL:
        return EstadoDeRevision.PENDIENTE

    hay_deseable_dudoso = any(
        nombre in CAMPOS_DESEABLES and not campo.es_fiable for nombre, campo in campos.items()
    )
    return EstadoDeRevision.PENDIENTE if hay_deseable_dudoso else EstadoDeRevision.NO_REQUERIDA


def confianza_global(campos: dict[str, CampoExtraido]) -> float:
    """
    Confianza del registro, ponderando más los imprescindibles. Una media simple
    permitiría que cinco campos accesorios perfectos compensaran un importe ilegible,
    y el registro parecería fiable cuando el dato que importa no lo es.
    """
    if not campos:
        return 0.0

    peso_total = 0.0
    acumulado = 0.0
    for nombre, campo in campos.items():
        peso = 3.0 if nombre in CAMPOS_IMPRESCINDIBLES else 1.0
        acumulado += campo.confianza * peso
        peso_total += peso

    return acumulado / peso_total


def estrategia_dominante(campos: dict[str, CampoExtraido]) -> Estrategia | None:
    """Motor que aportó más campos al resultado final (para afinar el orden del pipeline)."""
    if not campos:
        return None
    conteo: dict[Estrategia, int] = {}
    for campo in campos.values():
        conteo[campo.estrategia] = conteo.get(campo.estrategia, 0) + 1
    return max(conteo, key=lambda k: conteo[k])
