# Guía de despliegue — Plataforma

Arquitectura del despliegue:

```
                 Neon (PostgreSQL, SSL)
                          ▲
                          │
Railway ─ web (FastAPI)   │   Vercel ─ frontend (React SPA)
        ─ worker-sire     │            VITE_API_URL ─► dominio del web
        ─ worker-sunat  ──┘
        ─ worker-scanner
                          ▲
                 Cloudflare R2 / S3 (archivos)
                          ▲
                 Auth0 (identidad única)
```

- **Backend** (`plataforma-backend`) → Railway: 1 servicio `web` + 3 servicios `worker`.
- **Frontend** (`plataforma-frontend`) → Vercel.
- **Base de datos** → Neon.
- **Archivos generados** (reportes SIRE, descargas SUNAT, salidas del Scanner) → Cloudflare R2 o AWS S3.
- **Identidad** → Auth0 (tenant único ya configurado; ver `SETUP-AUTH0.md`).

> **Requisito de almacenamiento (no opcional en esta topología).**
> Los workers corren en servicios separados del `web`, cada uno con su propio disco,
> y Railway borra el disco en cada redeploy. Por eso **`STORAGE_BACKEND=s3`** es
> obligatorio: es la única forma de que el archivo que genera un worker sea servible
> por el `web`. Cloudflare R2 es la opción recomendada (compatible con S3, sin costo
> de egreso).

---

## 0) Subir a GitHub (2 repos privados)

Crea en github.com **dos repos vacíos y privados** (sin README ni .gitignore):
`plataforma-backend` y `plataforma-frontend`.

Luego, en cada carpeta local:

```bash
# Backend
cd C:/Apps/plataforma-backend
git branch -M main
git remote add origin https://github.com/<TU_USUARIO>/plataforma-backend.git
git push -u origin main
```

```bash
# Frontend
cd C:/Apps/plataforma-frontend
git branch -M main
git remote add origin https://github.com/<TU_USUARIO>/plataforma-frontend.git
git push -u origin main
```

El primer `push` abrirá el login de GitHub en tu gestor de credenciales de Windows.
Los `.env` están en `.gitignore` — no se suben; sí se sube `.env.example`.

---

## 1) Neon (PostgreSQL)

1. Crea un proyecto en [neon.tech](https://neon.tech). Región cercana (p. ej. AWS `us-east-2`).
2. En **Connection Details** copia el **Pooled connection string**.
3. Adáptalo al driver del proyecto (`+psycopg2`) y exige SSL:

   ```
   postgresql+psycopg2://USER:PASSWORD@ep-xxxx-pooler.us-east-2.aws.neon.tech/NEONDB?sslmode=require
   ```

   Ese valor será `DATABASE_URL` en Railway (mismo string para web y workers).

> Neon crea el esquema/tablas cuando corran las migraciones (paso 3.4). No hay que
> crear tablas a mano.

---

## 2) Generar la llave de cifrado (Fernet)

Las credenciales SUNAT se guardan cifradas. Genera **una llave propia** (la de dev
no arranca en producción):

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Guarda el resultado: será `ENCRYPTION_KEY` en Railway. **No la pierdas** — sin ella no
se pueden descifrar las credenciales ya guardadas.

---

## 3) Railway (backend: web + 3 workers)

### 3.1 Variables compartidas del proyecto

En Railway crea el proyecto desde el repo `plataforma-backend`. En
**Project → Variables** (compartidas por todos los servicios) define:

| Variable | Valor |
|---|---|
| `ENV` | `production` |
| `DEBUG` | `false` |
| `DATABASE_URL` | *(string pooled de Neon, con `+psycopg2` y `?sslmode=require`)* |
| `ENCRYPTION_KEY` | *(la llave generada en el paso 2)* |
| `AUTH0_DOMAIN` | `tu-tenant.us.auth0.com` |
| `AUTH0_AUDIENCE` | `https://api.plataforma` |
| `AUTH0_SPA_CLIENT_ID` | *(Client ID de la app SPA en Auth0)* |
| `AUTH0_MGMT_CLIENT_ID` | *(Client ID de la M2M Management API)* |
| `AUTH0_MGMT_CLIENT_SECRET` | *(secret de la M2M)* |
| `STORAGE_BACKEND` | `s3` |
| `S3_ENDPOINT_URL` | *(R2: `https://<accountid>.r2.cloudflarestorage.com`)* |
| `S3_REGION` | `auto` *(R2)* — para AWS S3 usa la región real |
| `S3_BUCKET` | `plataforma` |
| `S3_ACCESS_KEY` | *(access key de R2/S3)* |
| `S3_SECRET_KEY` | *(secret key de R2/S3)* |

### 3.2 Servicio `web`

- **Source:** repo `plataforma-backend` (Railway detecta el `Dockerfile`).
- **Start Command:** *(vacío — usa el `CMD` del Dockerfile)*.
- **Networking → Generate Domain:** genera el dominio público. Anótalo, será
  `https://plataforma-web-production.up.railway.app` (ejemplo).
- **Healthcheck Path:** `/health`.
- Variables **solo del web** (además de las compartidas):

  | Variable | Valor |
  |---|---|
  | `CORS_ORIGINS` | `["https://TU-APP.vercel.app"]` *(se completa en el paso 5)* |
  | `DRIVE_REDIRECT_URI` | `https://<dominio-web>/api/sunat/drive/callback` |
  | `GOOGLE_CLIENT_ID` | *(opcional — solo si usas export a Google Drive)* |
  | `GOOGLE_CLIENT_SECRET` | *(opcional)* |

### 3.3 Servicios worker (×3)

Crea **3 servicios más** en el mismo proyecto, todos desde el mismo repo
`plataforma-backend`. Cada uno:

- Hereda las variables compartidas del proyecto (paso 3.1).
- **NO** genera dominio ni healthcheck.
- Cambia solo el **Custom Start Command**:

  | Servicio | Start Command |
  |---|---|
  | `worker-sire` | `python -m workers.sire_worker` |
  | `worker-sunat` | `python -m workers.sunat_worker` |
  | `worker-scanner` | `python -m workers.scanner_worker` |

> Los 3 comparten la misma imagen Docker (ya trae Chromium para SUNAT y Tesseract
> español para Scanner). Puedes escalar cada worker de forma independiente.

### 3.4 Migraciones + seed (una vez, y en cada release)

En el servicio **`web`**, configura **Settings → Deploy → Pre-deploy Command**:

```
alembic upgrade head && python -m src.seed
```

- `alembic upgrade head` crea/actualiza las tablas en Neon.
- `python -m src.seed` registra roles, módulos y permisos base (es **idempotente**).

> Solo en el `web` (no en los workers) para evitar migraciones simultáneas.
> Si tu plan de Railway no expone Pre-deploy Command, corre una vez desde tu máquina:
> `railway run alembic upgrade head && railway run python -m src.seed`.

### 3.5 Crear el primer SuperAdmin

El seed no crea usuarios. Tras el primer deploy, marca tu usuario como admin de
plataforma. Inicia sesión una vez en el frontend (para que Auth0 cree el registro),
luego desde la consola SQL de Neon:

```sql
UPDATE core.users SET is_platform_admin = true WHERE email = 'tu-correo@ejemplo.com';
```

---

## 4) Vercel (frontend)

1. Importa el repo `plataforma-frontend` en [vercel.com](https://vercel.com).
2. Framework: **Vite** (autodetectado). Build: `npm run build`. Output: `dist`.
   El `vercel.json` ya incluye el rewrite SPA (recargar rutas no da 404).
3. **Environment Variables:**

   | Variable | Valor |
   |---|---|
   | `VITE_API_URL` | `https://<dominio-web-de-railway>` |
   | `VITE_AUTH0_DOMAIN` | `tu-tenant.us.auth0.com` |
   | `VITE_AUTH0_CLIENT_ID` | *(Client ID de la app SPA — el mismo `AUTH0_SPA_CLIENT_ID`)* |
   | `VITE_AUTH0_AUDIENCE` | `https://api.plataforma` |

4. Deploy. Anota el dominio, p. ej. `https://plataforma.vercel.app`.

> Las `VITE_*` se incrustan **en tiempo de build**: si cambias una, hay que
> **redeploy** en Vercel para que tome efecto.

---

## 5) Conectar todo (referencias cruzadas)

1. **Railway `web` → CORS:** pon el dominio de Vercel en `CORS_ORIGINS`:

   ```
   CORS_ORIGINS=["https://plataforma.vercel.app"]
   ```

   (Formato lista JSON. Puedes incluir varios: `["https://a.vercel.app","https://b.com"]`.)

2. **Vercel → API:** confirma que `VITE_API_URL` apunta al dominio del `web` de Railway
   y haz **redeploy**.

3. **Auth0 → Application (SPA)** — en Dashboard → Applications → tu SPA → Settings:

   | Campo | Valor |
   |---|---|
   | Allowed Callback URLs | `https://plataforma.vercel.app, http://localhost:5173` |
   | Allowed Logout URLs | `https://plataforma.vercel.app, http://localhost:5173` |
   | Allowed Web Origins | `https://plataforma.vercel.app, http://localhost:5173` |

4. **Google Drive (opcional, módulo SUNAT):** si usas export a Drive, en Google Cloud
   Console → OAuth client, agrega el **Authorized redirect URI**:
   `https://<dominio-web>/api/sunat/drive/callback` (igual a `DRIVE_REDIRECT_URI`).

---

## 6) Verificación post-deploy

- [ ] `https://<web>/health` responde `{"status":"ok"}`.
- [ ] `https://<web>/health/ready` responde `{"status":"ok","checks":{"database":true}}`.
- [ ] `https://<web>/docs` lista los endpoints (incluye `/api/tickets`).
- [ ] El frontend en Vercel carga y el login de Auth0 redirige de vuelta sin error.
- [ ] Tras marcar tu usuario como `is_platform_admin`, ves Administración.
- [ ] Logs de Railway: los 3 workers imprimen su arranque y quedan en polling.
- [ ] Prueba un job real (p. ej. una conciliación SIRE) y confirma que el archivo
      resultante se descarga desde el `web` (valida R2/S3 end-to-end).

---

## Referencia rápida de variables

**Backend (Railway) — todas las variables:** ver `.env.example`.
Obligatorias en producción: `ENV=production`, `DATABASE_URL`, `ENCRYPTION_KEY`,
`STORAGE_BACKEND=s3` + `S3_*`, `AUTH0_*`, `CORS_ORIGINS`.

**Frontend (Vercel):** `VITE_API_URL`, `VITE_AUTH0_DOMAIN`, `VITE_AUTH0_CLIENT_ID`,
`VITE_AUTH0_AUDIENCE`.
