# plataforma-backend

Plataforma SaaS unificada (Scanner + SUNAT + SIRE) — **monolito modular** con
FastAPI. Cada automatización es un módulo con arquitectura hexagonal sobre un
núcleo compartido (`platform/`): identidad (Auth0), tenencia multi-empresa,
autorización, storage, jobs y eventos.

## Documentación

La documentación completa vive en [`docs/`](./docs/): arquitectura, módulos,
plataforma, integraciones, operaciones y decisiones (ADR). Empieza por
[`docs/README.md`](./docs/README.md).

## Estructura

```
src/
├─ platform/        # núcleo compartido (config, database, web, modularidad, ...)
├─ modules/         # módulos de negocio (sire, sunat, scanner) — Fases 2-4
├─ module_registry  # lista de módulos activos
└─ main.py          # compone la app
workers/            # procesos para trabajos largos (OCR, Playwright, conciliación)
migrations/         # Alembic
infra/              # docker-compose de desarrollo (Postgres + Redis + MinIO)
tests/
```

## Puesta en marcha (desarrollo)

Requisitos: Python 3.12+, Docker.

```bash
# 1. Entorno virtual + dependencias
python -m venv venv
venv\Scripts\activate            # Windows (PowerShell: venv\Scripts\Activate.ps1)
pip install -e ".[dev]"

# 2. Variables de entorno
copy .env.example .env           # (cp en bash)

# 3. Infraestructura local (Postgres + Redis + MinIO)
docker compose -f infra/docker-compose.yml up -d

# 4. API
uvicorn src.main:app --reload

# 5. Workers (cada uno en OTRA terminal, con el venv activo)
python -m workers.sire_worker       # conciliaciones SIRE
python -m workers.scanner_worker    # extracción OCR de documentos
python -m workers.sunat_worker      # descargas SUNAT (Playwright)
```

> ⚠️ **Los workers son obligatorios.** SIRE (conciliaciones) y Scanner (OCR)
> encolan sus trabajos en Postgres (estado `en_cola`) y los procesan estos
> procesos aparte. Si el worker respectivo no corre, los trabajos se quedan
> encolados y nunca terminan.

- API:        http://localhost:8000
- Docs:       http://localhost:8000/docs
- Liveness:   http://localhost:8000/health
- Readiness:  http://localhost:8000/health/ready

## Calidad

```bash
pytest                 # tests
ruff check .           # lint
lint-imports           # fronteras entre módulos (arquitectura)
```

## Despliegue

Una imagen ([`Dockerfile`](./Dockerfile)) corrida como **4 servicios** desde el
mismo repo: `web` (comando por defecto) + 3 workers (`sunat`/`sire`/`scanner`,
sobreescriben el *start command*). Todos comparten base de datos y **storage S3**.

Guía completa (Neon + R2 + Railway + Vercel + Auth0):
[`docs/operations/deploy.md`](./docs/operations/deploy.md).

## Estado

Núcleo (identidad Auth0, tenencia multi-empresa, autorización, onboarding) y los
tres módulos de negocio (**SIRE**, **SUNAT**, **Scanner**) operativos. Las
conciliaciones SIRE requieren el proceso `worker` en ejecución.
