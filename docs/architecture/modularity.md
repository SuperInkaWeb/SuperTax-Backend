# Modularidad y fronteras

El valor de un monolito *modular* está en que las fronteras se **respeten**. Aquí
no dependen de la disciplina manual: se verifican en CI con `import-linter`.

## Los dos contratos

Definidos en [`pyproject.toml`](../../pyproject.toml) bajo `[tool.importlinter]`:

```toml
# 1) El núcleo no depende de los módulos
type = "forbidden"
source_modules   = ["src.platform"]
forbidden_modules = ["src.modules"]

# 2) Los módulos de negocio no se importan entre sí
type = "independence"
modules = ["src.modules.sire", "src.modules.sunat", "src.modules.scanner"]
```

Consecuencias prácticas:

- **`platform/` no importa de `modules/`.** El núcleo es reutilizable y no sabe
  qué módulos existen. La dirección de dependencia siempre es módulo → núcleo.
- **`sunat`, `sire` y `scanner` son islas.** No comparten código directamente. Si
  dos módulos necesitan lo mismo, ese código sube a `platform/`, no se importa de
  lado a lado. Por eso, p. ej., SUNAT tiene su propio `job_queue.py` en vez de
  reusar el de SIRE.

Esto es lo que garantiza que **agregar o tocar un módulo no rompa a otro** — la
propiedad que motivó todo el diseño (ver
[decisions/0001-monolito-modular.md](../decisions/0001-monolito-modular.md)).

## Cómo se verifica

```bash
lint-imports        # 2 contracts kept → fronteras intactas
```

Se corre en CI. Si un import cruza una frontera prohibida, falla el build con el
nombre del contrato roto y la cadena de imports culpable.

## Arquitectura hexagonal por módulo

Dentro de cada módulo, las dependencias apuntan hacia adentro:

```
api  ─►  application  ─►  domain
 └──────────┴──────────►  infrastructure  (implementa puertos)
```

- **api**: adaptador de entrada (HTTP). Depende de `application`.
- **application**: orquesta casos de uso. Define *qué* necesita (puertos), no
  *cómo*. Ej.: `job_service` recibe un `FileStorage` por inyección.
- **domain**: reglas puras, sin dependencias de framework.
- **infrastructure**: adaptadores de salida (repositorios SQLAlchemy, clientes
  externos, motores Playwright/OCR). Implementa lo que `application` pide.

## Reglas de estilo y linting

`ruff` con `select = ["E", "F", "I"]` (errores, pyflakes, orden de imports),
`line-length = 100`. Se excluyen del linter (no del build):

- `migrations/` — autogeneradas por Alembic.
- El **motor SIRE portado** (`sire/infrastructure/parser|report|sunat`,
  `reconciliation/engine.py`, `reconciliation/worker.py`) — se mantiene
  byte-idéntico a `sire-backend`; el código propio alrededor sí se lintea.
- `sunat/infrastructure/automation`, `scanner/infrastructure/{utils,extractor}`
  — código de automatización/extracción con líneas largas toleradas.

> Al validar un archivo suelto, ruff mostrará avisos E501 en estas rutas
> excluidas; CI los ignora. La verificación autoritativa es `ruff check .`.
