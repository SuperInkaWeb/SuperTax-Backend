"""Descriptor del módulo Scanner: contrato de auto-registro."""
from src.modules.scanner.api.routes import router
from src.platform.modularity import ModuleDescriptor

SCANNER_PERMISSIONS = (
    "scanner.doc.read",
    "scanner.doc.create",
    "scanner.doc.update",
)

descriptor = ModuleDescriptor(
    key="scanner",
    name="Escaneo de documentos",
    router=router,
    permissions=SCANNER_PERMISSIONS,
)
