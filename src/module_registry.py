"""
Registro central de módulos de negocio.

Cada módulo (Fases 2-4) expondrá un `ModuleDescriptor` y se agregará a esta
lista. `main.py` monta automáticamente lo que aquí figure. Hoy (Fase 0) está
vacío a propósito: la plataforma arranca sin módulos, solo con el núcleo.
"""
from src.platform.modularity import ModuleDescriptor

MODULES: list[ModuleDescriptor] = [
    # Fase 2 → from src.modules.sire.descriptor import descriptor as sire_descriptor
    # Fase 3 → sunat_descriptor
    # Fase 4 → scanner_descriptor
]
