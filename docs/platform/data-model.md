# Modelo de datos y migraciones

## Un esquema (schema) por módulo

Todas las tablas viven en una sola base Postgres, separadas por **esquema** según
a quién pertenecen. Cada modelo declara su esquema con
`__table_args__ = {"schema": "<nombre>"}`:

| Esquema | Dueño | Contenido |
|---|---|---|
| `core` | `platform/` | usuarios, estudios, empresas, membresías, roles, permisos, módulos, credenciales, solicitudes, tickets |
| `sunat` | módulo SUNAT | jobs de descarga, logs, resultados, credenciales, token de Drive |
| `sire` | módulo SIRE | jobs de conciliación, resultados, credenciales, sesiones |
| `scanner` | módulo Scanner | documentos, jobs de extracción, resultados |
| `datafilter` | módulo DataFilter | datasets (metadata del Parquet, estado de ingesta) |
| `correos` | módulo Correos | buzones (OAuth), escaneos, mensajes, adjuntos, registros extraídos, auditoría |

Esta separación refuerza la modularidad también en la base: cada módulo es dueño
de su esquema; lo compartido vive en `core`.

## Entidades de `core` (núcleo)

| Área | Tablas (models) |
|---|---|
| Identidad | `core.users` (`auth0_sub`, `email`, `status`, `is_platform_admin` = SuperAdmin) |
| Tenencia | `core.estudios` (inquilino, `owner_user_id`), `core.companies` (`ruc` único, `estudio_id`), `core.estudio_members` (admins del estudio), `core.memberships` (staff por empresa) |
| Autorización | `core.roles`, `core.permissions`, `core.role_permissions`, `core.modules`, `core.company_modules` (entitlements) |
| Credenciales | `core.company_credentials` (bóveda cifrada Fernet: Clave SOL / API SIRE por empresa) |
| Solicitudes | `core.access_requests` (alta de estudio → SuperAdmin), `core.company_access_requests` (acceso a empresa existente → admin dueño) |
| Soporte | tickets (`platform/support`) |

> **Dos tablas de acceso:** `estudio_members` = admins (ven toda la cartera);
> `memberships` = staff acotado a clientes (operador/asistente/consulta). Un admin
> nunca se guarda en `memberships`. Ver [../architecture/tenancy.md](../architecture/tenancy.md).

Definiciones en `src/platform/*/models.py`. Todos los modelos se registran en
`src/models_registry.py` (lo importan los workers para resolver las FKs a `core.*`).

## Entidades por módulo

- **`sunat`**: `SunatJobModel` (estado, config cifrada, insumo, `cancel_requested`),
  `SunatJobLogModel` (logs/progreso), resultados, `DriveTokenModel` (tokens de Drive
  cifrados **por usuario**).
- **`sire`**: jobs de conciliación, resultados, mapeos de columnas, sesiones.
- **`scanner`**: documentos y jobs de extracción con sus resultados.
- **`datafilter`**: `DatasetModel` (metadata del archivo subido y su Parquet: estado de
  ingesta, columnas, conteo, rutas en R2, `multipart_upload_id`). El archivo y el Parquet
  viven en storage (R2), no en la BD.
- **`correos`**: buzones (OAuth Gmail/Outlook), escaneos, mensajes, adjuntos, registros
  extraídos y auditoría propia del módulo.

> **Credenciales SOL / API SIRE**: hoy se centralizan en la bóveda
> `core.company_credentials` (cifrada con Fernet). Las tablas de credenciales por módulo
> (`sunat.*`, `sire.*`) quedan como respaldo (fallback) hasta retirarlas.

## Conexión (`platform/database/session.py`)

- `engine` con `pool_pre_ping` (revalida conexiones), `pool_recycle=300` (Neon
  cierra las ociosas) y TCP keepalives (para operaciones largas de polling).
- `get_db()` entrega **una sesión por request** y garantiza el cierre. Los
  repositorios la reciben por inyección; nunca abren su propia conexión.
- Los workers usan `SessionLocal()` directamente (fuera del ciclo de request).

## Migraciones (Alembic)

- Carpeta `migrations/`. Se aplican con `alembic upgrade head`.
- En producción corren **automáticamente al arrancar el web**:
  `bootstrap.ensure_database_ready()` (en el `lifespan`) ejecuta `alembic upgrade head`
  + el `seed`, protegido por un **advisory lock** de Postgres (solo una réplica migra).
  Desactivable con `DB_BOOTSTRAP_ON_STARTUP=false` para migrar en un paso dedicado.
- `python -m src.seed` registra roles, módulos y permisos base; es **idempotente**.
  Re-sembrar solo **agrega** permisos (nunca quita), así que correrlo tras un deploy
  aplica nuevos permisos/roles.

> Nunca se modifican tablas a mano en producción: todo cambio de esquema pasa por
> una migración versionada.
