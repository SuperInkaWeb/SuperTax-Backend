"""
Registro central de módulos de negocio.

Cada módulo (Fases 2-4) expondrá un `ModuleDescriptor` y se agregará a esta
lista. `main.py` monta automáticamente lo que aquí figure. Hoy (Fase 0) está
vacío a propósito: la plataforma arranca sin módulos, solo con el núcleo.
"""
from src.modules.scanner.descriptor import descriptor as scanner_descriptor
from src.modules.sire.descriptor import descriptor as sire_descriptor
from src.modules.sunat.descriptor import descriptor as sunat_descriptor
from src.platform.modularity import ModuleDescriptor

MODULES: list[ModuleDescriptor] = [
    sire_descriptor,
    sunat_descriptor,
    scanner_descriptor,
]
