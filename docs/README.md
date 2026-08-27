# Documentación — plataforma-backend

Plataforma SaaS unificada (**Scanner · SUNAT · SIRE**) construida como **monolito
modular** con FastAPI: tres módulos de negocio con arquitectura hexagonal sobre un
núcleo compartido (`platform/`).

Esta carpeta es la **fuente de verdad** de la documentación cross-cutting. El
frontend tiene su propia carpeta `docs/` para lo específico de la SPA.

## Por dónde empezar

| Si quieres… | Lee |
|---|---|
| Entender el sistema de un vistazo | [architecture/overview.md](architecture/overview.md) |
| Levantarlo en tu máquina | [operations/setup-local.md](operations/setup-local.md) |
| Desplegarlo a producción | [operations/deploy.md](operations/deploy.md) |
| Saber qué hace cada módulo | [modules/](modules/) |
| Configurar una variable | [operations/env-vars.md](operations/env-vars.md) |

## Índice

### Arquitectura
- [overview.md](architecture/overview.md) — visión general, capas y flujo de un request
- [modularity.md](architecture/modularity.md) — fronteras entre módulos (import-linter), hexagonal
- [async-jobs.md](architecture/async-jobs.md) — cola sobre Postgres + workers, ciclo de vida de un job
- [tenancy.md](architecture/tenancy.md) — multiempresa, empresa activa, aislamiento

### Módulos de negocio
- [sunat.md](modules/sunat.md) — descarga de comprobantes (híbrido Playwright + API consultacpe)
- [sire.md](modules/sire.md) — conciliación de registros de compras/ventas
- [scanner.md](modules/scanner.md) — extracción OCR + IA de documentos

### Plataforma (núcleo)
- [auth.md](platform/auth.md) — identidad Auth0, pipeline de seguridad, RBAC
- [auth-setup.md](platform/auth-setup.md) — cómo configurar el tenant de Auth0
- [security.md](platform/security.md) — cifrado, OWASP, defensas transversales
- [storage.md](platform/storage.md) — abstracción de archivos (local / S3 / R2)
- [data-model.md](platform/data-model.md) — esquemas, entidades y migraciones

### Integraciones
- [google-drive.md](integrations/google-drive.md) — subida de resultados (OAuth) + Picker de entrada
- [groq.md](integrations/groq.md) — visión IA para el Scanner

### Operaciones
- [setup-local.md](operations/setup-local.md) — entorno de desarrollo
- [deploy.md](operations/deploy.md) — Neon + R2 + Railway + Vercel + Auth0
- [env-vars.md](operations/env-vars.md) — todas las variables en una tabla

### Decisiones de arquitectura (ADR)
- [0001-monolito-modular.md](decisions/0001-monolito-modular.md)
- [0002-scope-drive-file.md](decisions/0002-scope-drive-file.md)
- [0003-sunat-consultacpe-hibrido.md](decisions/0003-sunat-consultacpe-hibrido.md)
