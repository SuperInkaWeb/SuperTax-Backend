# Modelo de datos y migraciones

## Un esquema (schema) por módulo

Todas las tablas viven en una sola base Postgres, separadas por **esquema** según
a quién pertenecen. Cada modelo declara su esquema con
`__table_args__ = {"schema": "<nombre>"}`:

| Esquema | Dueño | Contenido |
|---|---|---|
| `core` | `platform/` | usuarios, empresas, membresías, roles, permisos, módulos, tickets |
| `sunat` | módulo SUNAT | jobs de descarga, logs, resultados, credenciales, token de Drive |
| `sire` | módulo SIRE | jobs de conciliación, resultados, credenciales, sesiones |
| `scanner` | módulo Scanner | documentos, jobs de extracción, resultados |

Esta separación refuerza la modularidad también en la base: cada módulo es dueño
de su esquema; lo compartido vive en `core`.

## Entidades de `core` (núcleo)

| Área | Tablas (models) |
|---|---|
| Identidad | `core.users` (`auth0_sub`, `email`, `status`, `is_platform_admin`) |
| Tenencia | `core.companies`, `core.memberships` (usuario ↔ empresa + rol) |
| Autorización | `core.roles`, `core.permissions`, relación rol↔permiso, módulos/entitlements |
| Soporte | tickets (`platform/support`) |

Definiciones en `src/platform/*/models.py`. Todos los modelos se registran en
`src/models_registry.py` (lo importan los workers para resolver las FKs a `core.*`).

## Entidades por módulo

- **`sunat`** (`modules/sunat/infrastructure/models.py`): `SunatJobModel` (estado,
  config cifrada, ruta del insumo, `cancel_requested`), `SunatJobLogModel`
  (logs/progreso), resultados, credenciales SOL cifradas, `DriveTokenModel`
  (tokens de Drive cifrados por empresa).
- **`sire`** (`modules/sire/infrastructure/models.py`): jobs de conciliación,
  resultados, credenciales, sesiones.
- **`scanner`** (`modules/scanner/infrastructure/models.py`): documentos y jobs de
  extracción con sus resultados.

## Conexión (`platform/database/session.py`)

- `engine` con `pool_pre_ping` (revalida conexiones), `pool_recycle=300` (Neon
  cierra las ociosas) y TCP keepalives (para operaciones largas de polling).
- `get_db()` entrega **una sesión por request** y garantiza el cierre. Los
  repositorios la reciben por inyección; nunca abren su propia conexión.
- Los workers usan `SessionLocal()` directamente (fuera del ciclo de request).

## Migraciones (Alembic)

- Carpeta `migrations/` (excluida del linter por ser autogenerada).
- Se aplican con `alembic upgrade head`.
- En producción corren como **Pre-deploy Command** del servicio `web`
  (no en los workers, para evitar migraciones simultáneas):

  ```
  alembic upgrade head && python -m src.seed
  ```

- `python -m src.seed` registra roles, módulos y permisos base; es **idempotente**.

> Nunca se modifican tablas a mano en producción: todo cambio de esquema pasa por
> una migración versionada.
