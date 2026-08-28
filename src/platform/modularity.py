"""
Contrato de modularidad.

Cada módulo de negocio (sire, sunat, scanner) expone un `ModuleDescriptor`.
El registro central los recoge y `main.py` los monta automáticamente. Así,
agregar un módulo nuevo = crear su carpeta + su descriptor, sin tocar el núcleo
(principio Open/Closed).
"""
from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import APIRouter


@dataclass(frozen=True)
class ModuleDescriptor:
    key: str                                  # identificador: "sire", "sunat", "scanner"
    name: str                                 # nombre legible
    router: APIRouter                         # endpoints del módulo
    permissions: tuple[str, ...] = ()         # p.ej. ("sire.job.create", "sire.job.approve")
    requires: tuple[str, ...] = field(default_factory=tuple)  # otros módulos de los que depende
    # Se ejecuta una vez al arrancar el proceso web (p.ej. recuperar jobs
    # interrumpidos y re-despachar los que quedaron encolados). Opcional.
    on_startup: Callable[[], None] | None = None
