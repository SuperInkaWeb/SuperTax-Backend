"""Descriptor del módulo DataFilter: contrato de auto-registro."""
from src.modules.datafilter.api.routes import router
from src.platform.modularity import ModuleDescriptor

DATAFILTER_PERMISSIONS = (
    "datafilter.dataset.read",
    "datafilter.dataset.create",
    "datafilter.dataset.delete",
)

descriptor = ModuleDescriptor(
    key="datafilter",
    name="DataFilter — análisis de archivos",
    router=router,
    permissions=DATAFILTER_PERMISSIONS,
)
