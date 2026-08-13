# Configuración de Auth0 (tenant único)

Guía para conectar la plataforma con un **único tenant de Auth0**. El código ya
está listo; aquí solo se crean los recursos en el panel de Auth0 y se copian sus
valores a los `.env`.

> No se puede automatizar desde el repo: requiere tu cuenta de Auth0.

---

## 1. Crear el tenant

1. Entra a <https://auth0.com> → crea una cuenta (o usa la existente).
2. Crea un **tenant** (p. ej. `plataforma`). Su dominio será algo como
   `plataforma.us.auth0.com` → ese es tu **`AUTH0_DOMAIN`**.

## 2. Crear la API (define el `audience`)

1. **Applications → APIs → Create API**.
2. Name: `Plataforma API`. Identifier (audience): `https://api.plataforma`
   (es un identificador, no una URL real que deba existir).
3. Signing Algorithm: **RS256**.
4. Ese identifier es tu **`AUTH0_AUDIENCE`** (backend y frontend).

## 3. Crear la aplicación SPA (el frontend)

1. **Applications → Applications → Create Application** → tipo **Single Page**.
2. En **Settings**:
   - **Allowed Callback URLs**: `http://localhost:5173/dashboard`
   - **Allowed Logout URLs**: `http://localhost:5173/login`
   - **Allowed Web Origins**: `http://localhost:5173`
3. Copia el **Client ID** → es `AUTH0_SPA_CLIENT_ID` (backend) y
   `VITE_AUTH0_CLIENT_ID` (frontend).

## 4. Crear la aplicación M2M (Management API)

Necesaria para que el backend cree usuarios (onboarding e invitaciones).

1. **Applications → Applications → Create Application** → tipo
   **Machine to Machine**.
2. Autorízala contra la **Auth0 Management API** con los permisos (scopes):
   `create:users`, `read:users`, `update:users`, `delete:users`.
3. Copia **Client ID** y **Client Secret** → `AUTH0_MGMT_CLIENT_ID` y
   `AUTH0_MGMT_CLIENT_SECRET`.

## 5. Conexión de base de datos

En **Authentication → Database** existe por defecto
`Username-Password-Authentication`. Déjala habilitada para la SPA. Ese nombre es
`AUTH0_DB_CONNECTION` (ya viene por defecto en el código).

---

## 6. Variables resultantes

### Backend (`plataforma-backend/.env`)

```
AUTH0_DOMAIN=plataforma.us.auth0.com
AUTH0_AUDIENCE=https://api.plataforma
AUTH0_SPA_CLIENT_ID=xxxxxxxx        # Client ID de la SPA
AUTH0_MGMT_CLIENT_ID=yyyyyyyy       # Client ID de la M2M
AUTH0_MGMT_CLIENT_SECRET=zzzzzzzz   # Client Secret de la M2M
```

### Frontend (`plataforma-frontend/.env`)

```
VITE_API_URL=http://localhost:8000
VITE_AUTH0_DOMAIN=plataforma.us.auth0.com
VITE_AUTH0_CLIENT_ID=xxxxxxxx       # el mismo Client ID de la SPA
VITE_AUTH0_AUDIENCE=https://api.plataforma
```

---

## 7. Primer SuperAdmin

Auth0 autentica; los **permisos de plataforma** viven en la BD (`is_platform_admin`).
Tras iniciar sesión por primera vez con tu usuario, márcalo como SuperAdmin:

```sql
UPDATE core.users SET is_platform_admin = true WHERE email = 'tu-email@dominio.com';
```

(El usuario debe existir en `core.users`; se crea al validarse su token la primera
vez o al registrarlo por Auth0. Si aún no está, créalo en Auth0 e inicia sesión una vez.)

---

## 8. Probar el flujo completo

1. Backend: `uvicorn src.main:app --reload` (con Postgres arriba).
2. Frontend: `npm run dev`.
3. Entra a `http://localhost:5173` → **Iniciar sesión** (Auth0).
4. El backend valida el token, crea/lee el usuario en `core.users` y `/me`
   devuelve sus empresas.

> Multi-empresa avanzado: si más adelante usas **Auth0 Organizations** (una org
> por empresa), el `org_id` viaja en el token; hoy la empresa activa se resuelve
> con la cabecera `X-Company-Id` y las membresías, que ya funcionan sin Organizations.
