# Visión general de la arquitectura

## Qué es

Un **monolito modular**: una sola aplicación FastAPI que agrupa tres
automatizaciones tributarias (Scanner, SUNAT, SIRE) como **módulos
independientes** sobre un núcleo compartido. No son microservicios (un solo
proceso web, una sola base de datos), pero tampoco un monolito acoplado: las
fronteras entre módulos se verifican de forma automática (ver
[modularity.md](modularity.md)).

## Mapa de alto nivel

```
                         ┌──────────────────────────────┐
   React SPA  ──HTTP──►  │   FastAPI (servicio `web`)    │
 (Vercel)               │                                │
   Auth0 token           │   platform/  ← núcleo         │
   X-Company-Id          │   modules/sunat  sire  scanner│
                         └───────┬───────────────┬───────┘
                                 │               │ encola jobs
                          Postgres (Neon)        ▼
                                 ▲        ┌───────────────┐
                                 └────────│ workers ×3    │
                        Cloudflare R2/S3  │ (procesos      │
                          (archivos)      │  aparte)       │
                                          └───────────────┘
```

- **`web`**: atiende el HTTP, valida identidad y permisos, y **encola** los
  trabajos largos (no los ejecuta).
- **workers**: procesos separados que consumen la cola en Postgres y corren el
  trabajo pesado (Playwright para SUNAT, OCR para Scanner, conciliación para
  SIRE). Ver [async-jobs.md](async-jobs.md).
- **Postgres**: única base de datos, con un **esquema por módulo** + `core`.
- **Storage S3/R2**: archivos que un worker genera y el `web` sirve. Obligatorio
  en producción porque worker y web tienen discos distintos.

## Capas (dentro de cada módulo — hexagonal)

Cada módulo (`src/modules/<m>/`) respeta la separación estricta que exige el
estándar del proyecto:

```
api/            → routers FastAPI: reciben la petición, validan y delegan
application/    → lógica de negocio (services, casos de uso)
domain/         → entidades y reglas de dominio (cuando aplica)
infrastructure/ → acceso a datos, integraciones externas, motores
```

Un router **nunca** contiene lógica de negocio: traduce HTTP ↔ servicio y mapea
errores de negocio a códigos HTTP.

## El núcleo `platform/`

Servicios transversales que los módulos consumen, nunca al revés:

| Paquete | Responsabilidad |
|---|---|
| `config/` | configuración central desde variables de entorno |
| `database/` | motor y sesión SQLAlchemy (`get_db` por request) |
| `identity/` | validación del token Auth0, usuario actual |
| `tenancy/` | empresas, membresías, empresa activa |
| `authorization/` | roles, permisos (RBAC), entitlements por módulo |
| `storage/` | puerto `FileStorage` + adaptadores local/S3 |
| `onboarding/`, `users/`, `support/`, `web/` | registro, usuarios, tickets, health |

## Flujo de un request autenticado

```
1. HTTPBearer         → extrae el token del header Authorization
2. get_current_user   → valida el JWT contra Auth0, carga el User de core.users
3. get_active_context → lee X-Company-Id, valida membresía (403 si no es miembro)
4. require_module /    → comprueba que la empresa tiene el módulo y que el rol
   require_permission     tiene el permiso concreto de la operación
5. router → service   → lógica de negocio, respuesta
```

Este pipeline es la primera línea de defensa multi-tenant y RBAC; se detalla en
[platform/auth.md](../platform/auth.md).

## Repos relacionados

- **plataforma-frontend** — la SPA (React + Vite). Tiene su propia `docs/`.
- **sire-backend** — repo original de SIRE; el motor de conciliación se portó
  desde aquí *byte-idéntico* (ver [modules/sire.md](../modules/sire.md)).
