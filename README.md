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
```

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

## Estado

**Fase 0 — esqueleto.** Arranca el núcleo y responde `/health`. Sin módulos de
negocio todavía (se incorporan en fases siguientes).
