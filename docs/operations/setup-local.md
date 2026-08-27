# Entorno de desarrollo

Requisitos: **Python 3.12+** y **Docker**.

## Puesta en marcha

```bash
# 1. Entorno virtual + dependencias
python -m venv venv
venv\Scripts\activate            # Windows (PowerShell: venv\Scripts\Activate.ps1)
pip install -e ".[dev]"

# 2. Variables de entorno
copy .env.example .env           # (cp en bash) — rellena las claves de Auth0

# 3. Infraestructura local (Postgres + Redis + MinIO)
docker compose -f infra/docker-compose.yml up -d

# 4. Migraciones + seed
alembic upgrade head
python -m src.seed

# 5. API
uvicorn src.main:app --reload

# 6. Workers (cada uno en OTRA terminal, con el venv activo)
python -m workers.sunat_worker      # descargas SUNAT (Playwright)
python -m workers.sire_worker       # conciliaciones SIRE
python -m workers.scanner_worker    # extracción OCR de documentos
```

Playwright necesita el navegador una vez: `playwright install chromium`.

## URLs locales

| | URL |
|---|---|
| API | http://localhost:8000 |
| Docs (Swagger) | http://localhost:8000/docs |
| Liveness | http://localhost:8000/health |
| Readiness | http://localhost:8000/health/ready |

## Los workers son obligatorios

SUNAT, SIRE y Scanner **encolan** sus trabajos en Postgres y los procesan estos
procesos aparte. Si el worker de un módulo no corre, sus jobs se quedan `en_cola`
y nunca terminan. Ver [../architecture/async-jobs.md](../architecture/async-jobs.md).

## Configuración mínima para probar

- **Auth0**: sigue [../platform/auth-setup.md](../platform/auth-setup.md) y rellena
  `AUTH0_*` en `.env`. Sin esto no hay login.
- **Storage**: en local `STORAGE_BACKEND=local` (disco) es suficiente.
- **Cifrado**: la `ENCRYPTION_KEY` de dev viene por defecto (no usar en prod).
- **Google Drive / Groq**: opcionales; solo si pruebas esas funciones.

Lista completa de variables: [env-vars.md](env-vars.md).

## Calidad

```bash
pytest                 # tests
ruff check .           # lint (autoritativo)
lint-imports           # fronteras entre módulos (2 contracts kept)
```

## Primer SuperAdmin

Tras iniciar sesión una vez (para que se cree el usuario), márcalo en la BD:

```sql
UPDATE core.users SET is_platform_admin = true WHERE email = 'tu-correo@ejemplo.com';
```
