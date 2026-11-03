# Identidad, autenticación y autorización

Auth0 **autentica** (¿quién eres?). La **autorización** (¿a qué empresa perteneces
y qué puedes hacer?) vive en la base de datos de la plataforma. Para configurar el
tenant de Auth0 paso a paso, ver [auth-setup.md](auth-setup.md).

## El pipeline de seguridad

Toda ruta protegida encadena estas dependencias de FastAPI, en orden:

```
HTTPBearer                → extrae el token del header Authorization
  └─ get_current_user     → valida el JWT (Auth0) y carga el User de core.users
       └─ get_active_context   → resuelve la empresa activa (X-Company-Id) y valida acceso (admin de estudio o membresía)
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

`get_active_context` lee `X-Company-Id` y concede acceso si el usuario es **admin del
estudio dueño** (prevalece) **o** tiene **membresía directa** en la empresa; si no →
**403**. Devuelve `ActiveContext(user, company, role_id)` con el **rol efectivo**.
Detalle en [../architecture/tenancy.md](../architecture/tenancy.md).

### 3. Autorización por módulo y permiso (`platform/authorization/`)

- **Entitlements** (`entitlements.py`): ¿la empresa tiene el módulo contratado?
- **RBAC** (`permissions.py`): `role_has_permission(db, role_id, "sunat.drive.manage")`
  comprueba que el rol efectivo incluya el permiso pedido.

Los permisos son claves `<módulo>.<recurso>.<acción>`. Los **4 roles** se siembran con
`python -m src.seed` (idempotente): `admin_empresa` (admin de estudio, todo),
`operador` (contador, todo el módulo), `asistente` (día a día sin borrar ni
credenciales), `consulta` (solo lectura). El rol **admin no se asigna por empresa** (los
endpoints de asignación lo rechazan); se gestiona a nivel estudio.

## Registro, onboarding e invitaciones

El backend usa la **Management API M2M** de Auth0 para **provisionar** usuarios; el
correo de "establecer contraseña" lo maneja Auth0 (app SPA). No se usa el *Sign Up*
público de Auth0: las cuentas nacen por aprobación o invitación.

**Registro público en 2 pasos** (`platform/onboarding/`):
1. La persona ingresa el **RUC** → `GET /api/access-requests/verificar-ruc`.
2. Según el RUC:
   - **Libre** → solicitud de **alta de estudio** (`access_requests`). La aprueba el
     **SuperAdmin** → crea estudio + empresa + usuario + admin + módulos.
   - **Ocupado** → solicitud de **acceso** a la empresa existente
     (`company_access_requests`, sin cuenta aún). La aprueba el **admin dueño** →
     provisiona la cuenta + membresía con el rol elegido.

**Invitaciones (dentro de la app):** co-admin (`estudio_api`), staff a clientes
(`team_api` / `members_api`). Todas provisionan en Auth0 y envían el correo de
contraseña. Ver [auth-setup.md](auth-setup.md).

## Primer SuperAdmin

El seed no crea usuarios. Tras el primer login (que registra al usuario en
`core.users`), se marca a mano en la BD:

```sql
UPDATE core.users SET is_platform_admin = true WHERE email = 'tu-correo@ejemplo.com';
```
