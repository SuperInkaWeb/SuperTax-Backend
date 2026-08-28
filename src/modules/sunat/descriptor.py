"""Descriptor del módulo SUNAT: contrato de auto-registro."""
from src.modules.sunat.api.routes import router
from src.modules.sunat.infrastructure.job_queue import recuperar_pendientes
from src.platform.modularity import ModuleDescriptor

SUNAT_PERMISSIONS = (
    "sunat.job.read",
    "sunat.job.create",
    "sunat.credentials.manage",
    "sunat.drive.manage",
)

descriptor = ModuleDescriptor(
    key="sunat",
    name="Descarga SUNAT",
    router=router,
    permissions=SUNAT_PERMISSIONS,
    on_startup=recuperar_pendientes,
)
