"""Descriptor del módulo Correos: contrato de auto-registro."""
from src.modules.correos.api.routes import router
from src.platform.modularity import ModuleDescriptor

CORREOS_PERMISSIONS = (
    "correos.mailbox.read",
    "correos.mailbox.create",
    "correos.mailbox.delete",
    "correos.scan.read",
    "correos.scan.create",
    "correos.record.read",
    "correos.record.update",
    "correos.export.create",
)

descriptor = ModuleDescriptor(
    key="correos",
    name="Automatización de correos",
    router=router,
    permissions=CORREOS_PERMISSIONS,
)
