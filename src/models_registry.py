"""
Registro único de todos los modelos ORM en `Base.metadata`.

Importar este módulo tiene el efecto de registrar todas las tablas (núcleo +
módulos). Es necesario en cualquier proceso que no cargue la app completa —los
workers, las migraciones y los tests—: los modelos de los módulos declaran claves
foráneas hacia tablas del núcleo (p. ej. `sire.reconciliation_jobs.created_by_id`
→ `core.users.id`), y SQLAlchemy necesita que esas tablas estén en la metadata
para resolver los FKs al hacer flush.

Vive a nivel de `src` (no dentro de `platform/`) porque referencia los módulos, y
el núcleo nunca debe depender de los módulos.
"""
# Todos los modelos (núcleo + módulos); el import registra sus tablas.
from src.modules.scanner.infrastructure import models as _scanner  # noqa: F401
from src.modules.sire.infrastructure import models as _sire  # noqa: F401
from src.modules.sunat.infrastructure import models as _sunat  # noqa: F401
from src.platform.authorization import models as _authz  # noqa: F401
from src.platform.onboarding import models as _onboarding  # noqa: F401
from src.platform.support import models as _support  # noqa: F401
from src.platform.tenancy import models as _tenancy  # noqa: F401
from src.platform.users import models as _users  # noqa: F401
