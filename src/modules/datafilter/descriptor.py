"""Descriptor del módulo DataFilter: contrato de auto-registro."""
from src.modules.datafilter.api.analysis_routes import router as analysis_router
from src.modules.datafilter.api.routes import router
from src.modules.datafilter.infrastructure.ingest_dispatch import recuperar_pendientes
from src.platform.modularity import ModuleDescriptor

# Las rutas de análisis viven en su propio archivo (legibilidad); se montan bajo el
# mismo router del módulo (/api/datafilter).
router.include_router(analysis_router)

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
    on_startup=recuperar_pendientes,
)
