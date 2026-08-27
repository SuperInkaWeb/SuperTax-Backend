# Identidad, autenticación y autorización

Auth0 **autentica** (¿quién eres?). La **autorización** (¿a qué empresa perteneces
y qué puedes hacer?) vive en la base de datos de la plataforma. Para configurar el
tenant de Auth0 paso a paso, ver [auth-setup.md](auth-setup.md).

## El pipeline de seguridad

Toda ruta protegida encadena estas dependencias de FastAPI, en orden:

```
HTTPBearer                → extrae el token del header Authorization
  └─ get_current_user     → valida el JWT (Auth0) y carga el User de core.users
       └─ get_active_context   → resuelve la empresa activa (X-Company-Id) y valida membresía
            └─ require_module        → la empresa tiene habilitado el módulo
            └─ require_permission    → el rol del usuario tiene el permiso concreto
```

Cada eslabón corta el request con el código correcto (401/403/404) antes de llegar
a la lógica. Si uno falla, los siguientes no corren.

### 1. Autenticación (`platform/identity/`)

`get_current_user` valida el token contra Auth0 (`auth0.validar_token`, RS256 con
JWKS del tenant) y busca al usuario por `auth0_sub` en `core.users`:

- Token inválido/expirado → **401**.
- Usuario no registrado en la plataforma → **401**.
- Usuario `status != activo` → **403**.

### 2. Empresa activa (`platform/tenancy/`)

`get_active_context` lee `X-Company-Id`, valida la membresía y devuelve
`ActiveContext(user, company, membership)`. Sin membresía → **403**. Detalle en
[../architecture/tenancy.md](../architecture/tenancy.md).

### 3. Autorización por módulo y permiso (`platform/authorization/`)

- **Entitlements** (`entitlements.py`): ¿la empresa tiene el módulo contratado?
- **RBAC** (`permissions.py`): `role_has_permission(db, role_id, "sunat.drive.manage")`
  comprueba que el rol de la membresía incluya el permiso pedido.

Los permisos son claves con formato `<módulo>.<recurso>.<acción>` (p. ej.
`sunat.drive.manage`). Roles, permisos y su relación se siembran con `python -m src.seed`
(idempotente). Las APIs `roles_api` gestionan roles.

## Onboarding e invitaciones

El backend usa la **Management API M2M** de Auth0 para crear usuarios al invitarlos
(`platform/onboarding/`, `platform/users/`). El correo de "establecer contraseña"
lo maneja Auth0 vía la app SPA. Por eso se necesitan tanto la app SPA como la M2M
(ver [auth-setup.md](auth-setup.md)).

## Primer SuperAdmin

El seed no crea usuarios. Tras el primer login (que registra al usuario en
`core.users`), se marca a mano en la BD:

```sql
UPDATE core.users SET is_platform_admin = true WHERE email = 'tu-correo@ejemplo.com';
```
