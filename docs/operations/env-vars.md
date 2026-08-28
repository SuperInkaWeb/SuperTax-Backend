# Variables de entorno

Fuente única: [`src/platform/config/settings.py`](../../src/platform/config/settings.py)
(backend) y `import.meta.env` (frontend). Los `.env` no se versionan; sí
`.env.example`.

## Backend (Railway en producción)

### Aplicación
| Variable | Default | Notas |
|---|---|---|
| `ENV` | `development` | `production` en prod (activa validaciones) |
| `DEBUG` | `true` | `false` en prod |
| `APP_NAME` | `Plataforma` | |

### Base de datos
| Variable | Default | Notas |
|---|---|---|
| `DATABASE_URL` | postgres local :5433 | Neon con `+psycopg2` y `?sslmode=require` |

### Auth0 (obligatorias)
| Variable | Notas |
|---|---|
| `AUTH0_DOMAIN` | `tu-tenant.us.auth0.com` |
| `AUTH0_AUDIENCE` | identificador de la API, p. ej. `https://api.plataforma` |
| `AUTH0_SPA_CLIENT_ID` | Client ID de la app SPA |
| `AUTH0_MGMT_CLIENT_ID` | Client ID de la M2M (Management API) |
| `AUTH0_MGMT_CLIENT_SECRET` | **secreto** de la M2M |
| `AUTH0_DB_CONNECTION` | `Username-Password-Authentication` (default) |

### Storage
| Variable | Default | Notas |
|---|---|---|
| `STORAGE_BACKEND` | `local` | `s3` **obligatorio** en prod |
| `STORAGE_LOCAL_PATH` | `./storage` | solo backend `local` |
| `S3_ENDPOINT_URL` | vacío (AWS) | R2: `https://<accountid>.r2.cloudflarestorage.com` |
| `S3_REGION` | `us-east-1` | R2 usa `auto` |
| `S3_BUCKET` | `plataforma` | |
| `S3_ACCESS_KEY` / `S3_SECRET_KEY` | — | **secretos** (R2/S3) |

### Cifrado (obligatoria en prod)
| Variable | Notas |
|---|---|
| `ENCRYPTION_KEY` | Fernet. En prod **no** puede ser la de dev (el arranque lo impide) |

### Jobs / SUNAT / Scanner
| Variable | Default | Notas |
|---|---|---|
| `SUNAT_POLL_TIMEOUT_MINUTES` | `90` | |
| `SUNAT_MAX_CONCURRENCY` | `2` | descargas SUNAT en paralelo (on-demand en el `web`); cada una levanta Chromium |
| `DESCARGAS_DIR` | vacío (temp del SO) | dir de descargas del worker |
| `GOOGLE_CLIENT_ID` | vacío | Drive: subida (OAuth backend) |
| `GOOGLE_CLIENT_SECRET` | vacío | **secreto**; Drive subida |
| `DRIVE_REDIRECT_URI` | localhost callback | `https://<web>/api/sunat/drive/callback` |
| `SCANNER_TESSERACT_CMD` | `tesseract` | ejecutable de Tesseract |
| `GROQ_API_KEY` | — | fallback IA del Scanner (Groq) |
| `GROQ_MODEL` | `qwen/qwen3.6-27b` | modelo de visión |

### CORS
| Variable | Default | Notas |
|---|---|---|
| `CORS_ORIGINS` | `http://localhost:5173` | lista por comas; **nunca `*`** en prod |

## Frontend (Vercel) — todas PÚBLICAS

Las `VITE_*` se **incrustan en el bundle** en tiempo de build: son públicas por
diseño. En Vercel se declaran como *Config*, no como *Secret*. Cambiar una exige
**redeploy**.

| Variable | Notas |
|---|---|
| `VITE_API_URL` | dominio del `web` de Railway |
| `VITE_AUTH0_DOMAIN` | mismo tenant Auth0 |
| `VITE_AUTH0_CLIENT_ID` | mismo Client ID de la SPA |
| `VITE_AUTH0_AUDIENCE` | mismo audience |
| `VITE_GOOGLE_CLIENT_ID` | Picker de Drive (entrada) |
| `VITE_GOOGLE_API_KEY` | Picker de Drive (entrada) |

> El único secreto real de Google es `GOOGLE_CLIENT_SECRET` (backend/Railway). El
> `VITE_GOOGLE_CLIENT_ID` y el App ID (prefijo del client_id) son públicos.

## Obligatorias en producción (resumen)

`ENV=production`, `DATABASE_URL`, `ENCRYPTION_KEY`, `STORAGE_BACKEND=s3` + `S3_*`,
`AUTH0_*`, `CORS_ORIGINS`. Detalle de despliegue en [deploy.md](deploy.md).
