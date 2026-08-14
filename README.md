# plataforma-backend

Plataforma SaaS unificada (Scanner + SUNAT + SIRE) — **monolito modular** con
FastAPI. Cada automatización es un módulo con arquitectura hexagonal sobre un
núcleo compartido (`platform/`): identidad (Auth0), tenencia multi-empresa,
autorización, storage, jobs y eventos.

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

Se despliega con el [`Dockerfile`](./Dockerfile) (una sola imagen) corrida como
**dos servicios** desde el mismo repo:

| Servicio | Comando | Rol |
|----------|---------|-----|
| **web** (comando por defecto de la imagen) | `uvicorn src.main:app --host 0.0.0.0 --port $PORT` | API HTTP |
| **sire-worker** (sobreescribe el comando) | `python -m workers.sire_worker` | Procesa conciliaciones SIRE |
| **scanner-worker** (sobreescribe el comando) | `python -m workers.scanner_worker` | Procesa OCR de documentos |

En Railway/Render se crean estos servicios apuntando al mismo repo: **web** usa
el comando por defecto del `Dockerfile`; cada **worker** sobreescribe el *start
command*. Todos comparten la misma base de datos y **el mismo storage S3** (los
workers leen/escriben archivos que la API sirve; con disco local no funcionaría).

**Migraciones**: ejecutar `alembic upgrade head` como comando de *pre-deploy* del
servicio web (o tras cada cambio de esquema).

## Estado

Núcleo (identidad Auth0, tenencia multi-empresa, autorización, onboarding) y los
tres módulos de negocio (**SIRE**, **SUNAT**, **Scanner**) operativos. Las
conciliaciones SIRE requieren el proceso `worker` en ejecución.
