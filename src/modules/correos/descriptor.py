"""Descriptor del módulo Correos: contrato de auto-registro."""
from src.modules.correos.api.routes import router
from src.modules.correos.infrastructure.scan_dispatch import recuperar_pendientes
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
    # Sufijo `.view` (no .read): solo admin_empresa/supervisor lo reciben en el seed;
    # la bitácora de auditoría no debe verla cualquier rol de consulta.
    "correos.audit.view",
)

descriptor = ModuleDescriptor(
    key="correos",
    name="Automatización de correos",
    router=router,
    permissions=CORREOS_PERMISSIONS,
    on_startup=recuperar_pendientes,
)
