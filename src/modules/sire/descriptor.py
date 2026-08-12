"""
Descriptor del módulo SIRE: su contrato de auto-registro.

`module_registry` lo recoge y `main.py` monta su router bajo /api/sire y expone
sus permisos al seed. Agregar un endpoint o permiso aquí no toca el núcleo.
"""
from src.modules.sire.api.routes import router
from src.platform.modularity import ModuleDescriptor

SIRE_PERMISSIONS = (
    "sire.job.read",
    "sire.job.create",
    "sire.job.approve",
    "sire.credentials.manage",
)

descriptor = ModuleDescriptor(
    key="sire",
    name="Automatización SIRE",
    router=router,
    permissions=SIRE_PERMISSIONS,
)
